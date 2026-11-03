import base64
import hashlib
import json
import logging
import os
import time
from pathlib import Path
import httpx
from .sandbox import SandboxedWorker

log = logging.getLogger("pcc.agent")


def post(agent, path, data):
    response = agent.client.post(path, json=data)
    response.raise_for_status()
    return response.json()


def execute(agent, payload):
    job_id = payload["job_id"]
    base = "/internal/v1/jobs/"+job_id
    lease = {"attempt_id": payload["attempt_id"], "lease_token": payload["lease_token"]}
    worker = None
    synthetic_service = None
    journal = agent.config.work_root / "active-attempt.json"
    # Persistent record aids manual reconciliation; never assume an old child stopped.
    fd = os.open(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump({"job_id": job_id, **lease}, file)
    confirmed = False
    try:
        agent.heartbeat()
        if agent.config.mode in {"maintenance", "offline"}:
            raise RuntimeError("Local policy stopped new work")
        renewed = post(agent, base+"/renew", lease)
        if renewed["cancel_requested"]:
            raise RuntimeError("Cancellation requested")
        expires = renewed["lease_expires_at"]
        if payload["template"]["worker"].startswith("ollama:"):
            from .services import InferenceWorker
            worker = InferenceWorker(agent.services, payload)
        else:
            if payload["template"]["worker"].startswith("synthetic.") and hasattr(agent, "services"):
                synthetic_service = agent.services.acquire(payload["template"]["id"])
                agent.services.ready(synthetic_service)
            worker = SandboxedWorker(agent.config, payload)
        post(agent, base+"/events", {**lease, "sequence": 0, "message": "受控 Worker 已启动", "progress": 0.1})
        next_renew = time.monotonic()+2
        next_heartbeat = time.monotonic()+agent.config.heartbeat_seconds
        while worker.poll() is None:
            if time.time() >= min(expires-1, payload["deadline"]):
                raise RuntimeError("Local lease deadline reached")
            if time.monotonic() >= next_heartbeat:
                agent.heartbeat()
                next_heartbeat = time.monotonic()+agent.config.heartbeat_seconds
            if time.monotonic() >= next_renew:
                renewed = post(agent, base+"/renew", lease)
                expires = renewed["lease_expires_at"]
                if renewed["cancel_requested"]:
                    raise RuntimeError("Cancellation requested")
                next_renew = time.monotonic()+2
            time.sleep(0.1)
        output = worker.result()
        # Completed inference may stay loaded until its idle policy expires.
        renewed = post(agent, base+"/renew", lease)
        if renewed["cancel_requested"]:
            raise RuntimeError("Cancellation requested")
        artifact_ids = []
        for artifact in output.get("artifacts", []):
            data = base64.b64decode(artifact["data_base64"], validate=True)
            if len(data)>payload["limits"]["output_bytes"]:
                raise RuntimeError("Artifact too large")
            art = post(agent, base+"/artifacts", {**lease, **artifact, "sha256": hashlib.sha256(data).hexdigest()})
            artifact_ids.append(art["artifact_id"])
        post(agent, base+"/complete", {**lease, "result": output["result"], "artifact_ids": artifact_ids})
        confirmed = True
    except Exception as exc:
        if worker:
            worker.stop()
        try:
            post(agent, base+"/fail", {**lease, "stopped": True, "retryable": False, "message": "执行已停止："+type(exc).__name__})
            confirmed = True
        except httpx.HTTPError:
            log.warning("Result acknowledgement unavailable; attempt requires reconciliation")
    finally:
        if worker:
            worker.close()
        if synthetic_service:
            agent.services.release(synthetic_service)
        if confirmed:
            journal.unlink(missing_ok=True)
