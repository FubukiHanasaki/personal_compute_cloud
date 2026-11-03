import json
import logging
import random
import time
import uuid
import ssl
from pathlib import Path
import httpx
from .config import AgentConfig
from .platform import discover, isolation_capabilities, secure_windows_path

log = logging.getLogger("pcc.agent")


class Agent:
    def __init__(self, config_path, probe_isolation=True):
        self.config_path = Path(config_path)
        self.config = AgentConfig.load(config_path)
        self.config.work_root.mkdir(parents=True, exist_ok=True)
        secure_windows_path(self.config.work_root)
        self.identity = json.loads(self.config.credential_file.read_text())
        self.client = httpx.Client(
            base_url=self.config.server.rstrip("/"),
            headers={"Authorization": "Bearer " + self.identity["credential"]},
            timeout=35,
            follow_redirects=False,
            trust_env=False,
            verify=ssl.create_default_context(cafile=str(self.config.ca_file))
            if self.config.ca_file
            else True,
        )
        self.isolation = (
            isolation_capabilities(self.config)
            if probe_isolation
            and not (self.config.work_root / "active-attempt.json").exists()
            else []
        )
        from .services import ServiceManager

        self.services = ServiceManager(self.config)

    def heartbeat(self):
        config = AgentConfig.load(self.config_path)
        # Local policy can change without restarting, identity and transport cannot.
        editable = {
            key: getattr(config, key)
            for key in (
                "mode",
                "allowed_users",
                "allocatable_cpu",
                "allocatable_memory_mb",
                "allocatable_vram_mb",
                "max_cpu_load",
                "max_memory_load",
                "enable_compute",
                "enable_synthetic_ai",
            )
        }
        config = self.config.model_copy(update=editable)
        self.config = config
        caps = discover(config, self.isolation)
        self.latest_capabilities = caps
        response = self.client.post(
            "/internal/v1/nodes/heartbeat",
            json={
                "capabilities": caps,
                "policy": {
                    "mode": config.mode,
                    "allowed_users": config.allowed_users,
                    "max_cpu_load": config.max_cpu_load,
                    "max_memory_load": config.max_memory_load,
                    "max_concurrency": 1,
                },
                "services": self.services.reports(),
            },
        )
        response.raise_for_status()
        self.identity["owner_id"] = response.json().get("owner_id")
        self.services.config = config
        self.services.sync(response.json().get("services", []))
        return response.json()

    def run(self, once=False):
        try:
            self._run_loop(once)
        finally:
            try:
                self.services.stop_all()
            finally:
                self.client.close()

    def _run_loop(self, once=False):
        backoff = 1
        while True:
            try:
                self.heartbeat()
                backoff = 1
                journal = self.config.work_root / "active-attempt.json"
                if journal.exists():
                    log.error(
                        "Unacknowledged attempt present: inspect worker termination before reconciliation"
                    )
                    if once:
                        return
                    time.sleep(self.config.heartbeat_seconds)
                    continue
                self.services.tick()
                if once:
                    return
                pending = self.config.work_root / "pending-claim.json"
                if pending.exists():
                    claim_id = json.loads(pending.read_text())["claim_id"]
                else:
                    claim_id = uuid.uuid4().hex
                    with pending.open("x") as file:
                        json.dump({"claim_id": claim_id}, file)
                sent = time.monotonic()
                response = self.client.post(
                    "/internal/v1/jobs/claim",
                    json={
                        "claim_id": claim_id,
                        "wait_seconds": min(5, self.config.heartbeat_seconds),
                    },
                )
                response.raise_for_status()
                if response.status_code == 200:
                    from .execution import execute

                    payload = response.json()
                    payload["_local_deadline"] = sent + max(
                        0, payload["deadline"] - payload["server_time"]
                    )
                    execute(self, payload)
                pending.unlink(missing_ok=True)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                log.warning(
                    "Connection unavailable (%s); local policy remains authoritative",
                    type(exc).__name__,
                )
                if once:
                    raise
                time.sleep(min(60, backoff) + random.random())
                backoff = min(60, backoff * 2)
