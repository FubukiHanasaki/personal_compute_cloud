import base64
import hashlib
import json
import logging
import os
import time
import threading
import httpx
from .sandbox import SandboxedWorker
from .errors import WorkerStopUnconfirmed

log = logging.getLogger("pcc.agent")


def post(agent, path, data):
    sent = time.monotonic()
    response = agent.client.post(path, json=data)
    response.raise_for_status()
    result = response.json()
    if "lease_expires_at" in result:
        result["_local_lease_deadline"] = sent + max(
            0, result["lease_expires_at"] - result.get("server_time", time.time())
        )
    return result


def execute(agent, payload):
    import psutil

    job_id = payload["job_id"]
    base = "/internal/v1/jobs/" + job_id
    lease = {"attempt_id": payload["attempt_id"], "lease_token": payload["lease_token"]}
    worker = None
    synthetic_service = None
    journal = agent.config.work_root / "active-attempt.json"
    identity = {
        "job_id": job_id,
        **lease,
        "controller_pid": os.getpid(),
        "controller_created_at": psutil.Process().create_time(),
    }
    # Persistent record aids manual reconciliation; never assume an old child stopped.
    fd = os.open(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(identity, file)
    confirmed = False
    guardian_done = threading.Event()
    guardian_expired = threading.Event()
    guardian = None
    completion = None
    local_deadline = payload.get(
        "_local_deadline",
        time.monotonic()
        + max(0, payload["deadline"] - payload.get("server_time", time.time())),
    )
    try:
        agent.heartbeat()
        if agent.config.mode in {"maintenance", "offline"}:
            raise RuntimeError("Local policy stopped new work")
        if agent.config.mode == "private" and (
            not payload.get("owner_id")
            or payload.get("owner_id") != getattr(agent, "identity", {}).get("owner_id")
        ):
            raise RuntimeError("Local private policy refuses this owner")
        if (
            agent.config.mode == "reserved"
            and payload.get("owner_id") not in agent.config.allowed_users
        ):
            raise RuntimeError("Local reservation refuses this owner")
        kind = payload["template"]["worker"]
        if kind == "compute.stats" and not agent.config.enable_compute:
            raise RuntimeError("Local compute adapter was disabled")
        if kind.startswith("synthetic.") and not agent.config.enable_synthetic_ai:
            raise RuntimeError("Local synthetic adapter was disabled")
        for key in ("cpu", "memory_mb", "vram_mb"):
            if payload.get("limits", {}).get(key, 0) > getattr(
                agent.config, "allocatable_" + key
            ):
                raise RuntimeError("Claim exceeds current local resource allowance")
        caps = getattr(agent, "latest_capabilities", {})
        if (
            caps.get("cpu_load", 0) >= agent.config.max_cpu_load
            or caps.get("memory_load", 0) >= agent.config.max_memory_load
        ):
            raise RuntimeError("Local load protection stopped new work")
        renewed = post(agent, base + "/renew", lease)
        if renewed["cancel_requested"]:
            raise RuntimeError("Cancellation requested")
        expires = [renewed["_local_lease_deadline"]]

        def guard_lease():
            while not guardian_done.wait(0.1):
                if time.monotonic() >= min(expires[0] - 1, local_deadline):
                    guardian_expired.set()
                    if worker is not None:
                        try:
                            worker.stop()
                        except Exception:
                            log.error("Worker stop could not be confirmed")
                        return

        guardian = threading.Thread(target=guard_lease, daemon=True)
        guardian.start()
        if time.monotonic() >= min(expires[0] - 1, local_deadline):
            raise RuntimeError("Lease expired before worker startup")
        if payload["template"]["worker"].startswith(("ollama:", "comfyui:")):
            from .services import InferenceWorker

            worker = InferenceWorker(agent.services, payload)
        else:
            if payload["template"]["worker"].startswith("synthetic.") and hasattr(
                agent, "services"
            ):
                synthetic_service = agent.services.acquire(payload["template"]["id"])
                agent.services.ready(synthetic_service)
            worker = SandboxedWorker(
                agent.config,
                {
                    **payload,
                    "_local_deadline": local_deadline,
                    "_local_start_deadline": min(expires[0] - 1, local_deadline),
                },
            )
        if guardian_expired.is_set() or time.monotonic() >= min(
            expires[0] - 1, local_deadline
        ):
            raise RuntimeError("Lease expired during worker startup")
        record = dict(identity)
        if hasattr(worker, "process"):
            record["pid"] = worker.process.pid
            try:
                record["process_created_at"] = psutil.Process(
                    worker.process.pid
                ).create_time()
            except psutil.NoSuchProcess:
                pass
        elif getattr(getattr(worker, "service", None), "backend", None):
            backend = worker.service.backend
            record.update(backend.recovery_record())
        with journal.open("w") as file:
            json.dump(record, file)

        post(
            agent,
            base + "/events",
            {**lease, "sequence": 0, "message": "受控 Worker 已启动", "progress": 0.1},
        )
        next_renew = time.monotonic() + 2
        next_heartbeat = time.monotonic() + agent.config.heartbeat_seconds
        while worker.poll() is None:
            if time.monotonic() >= min(expires[0] - 1, local_deadline):
                raise RuntimeError("Local lease deadline reached")
            if time.monotonic() >= next_heartbeat:
                agent.heartbeat()
                next_heartbeat = time.monotonic() + agent.config.heartbeat_seconds
            if time.monotonic() >= next_renew:
                renewed = post(agent, base + "/renew", lease)
                expires[0] = renewed["_local_lease_deadline"]
                if renewed["cancel_requested"]:
                    raise RuntimeError("Cancellation requested")
                next_renew = time.monotonic() + 2
            time.sleep(0.1)
        if guardian_expired.is_set():
            raise RuntimeError("Lease guard stopped execution")
        output = worker.result()
        # Completed inference may stay loaded until its idle policy expires.
        renewed = post(agent, base + "/renew", lease)
        expires[0] = renewed["_local_lease_deadline"]
        if renewed["cancel_requested"]:
            raise RuntimeError("Cancellation requested")
        artifact_ids = []
        for artifact in output.get("artifacts", []):
            data = base64.b64decode(artifact["data_base64"], validate=True)
            if len(data) > payload["limits"]["output_bytes"]:
                raise RuntimeError("Artifact too large")
            art = post(
                agent,
                base + "/artifacts",
                {**lease, **artifact, "sha256": hashlib.sha256(data).hexdigest()},
            )
            artifact_ids.append(art["artifact_id"])
        completion = {**lease, "result": output["result"], "artifact_ids": artifact_ids}
        post(agent, base + "/complete", completion)
        confirmed = True
    except Exception as exc:
        if isinstance(exc, WorkerStopUnconfirmed):
            raise
        if worker:
            worker.stop()
        try:
            if completion is not None:
                # A lost completion response must be retried byte-for-byte.
                post(agent, base + "/complete", completion)
                confirmed = True
            if not confirmed:
                post(
                    agent,
                    base + "/fail",
                    {
                        **lease,
                        "stopped": True,
                        "retryable": False,
                        "message": "执行已停止：" + type(exc).__name__,
                    },
                )
            confirmed = True
        except httpx.HTTPError:
            log.warning(
                "Result acknowledgement unavailable; attempt requires reconciliation"
            )
    finally:
        guardian_done.set()
        if guardian:
            guardian.join(timeout=15)
        if worker:
            worker.close()
        if synthetic_service:
            agent.services.release(synthetic_service)
        if confirmed:
            journal.unlink(missing_ok=True)
