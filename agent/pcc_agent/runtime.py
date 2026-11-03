import json
import logging
import random
import time
import uuid
from pathlib import Path
import httpx
from .config import AgentConfig
from .platform import discover, isolation_capabilities

log = logging.getLogger("pcc.agent")


class Agent:
    def __init__(self, config_path):
        self.config_path = Path(config_path)
        self.config = AgentConfig.load(config_path)
        self.config.work_root.mkdir(parents=True, exist_ok=True)
        self.identity = json.loads(self.config.credential_file.read_text())
        self.client = httpx.Client(base_url=self.config.server.rstrip("/"), headers={"Authorization": "Bearer "+self.identity["credential"]}, timeout=35, follow_redirects=False, trust_env=False)
        self.isolation = isolation_capabilities()
        from .services import ServiceManager
        self.services = ServiceManager(self.config)

    def heartbeat(self):
        config = AgentConfig.load(self.config_path)
        # Local policy can change without restarting, identity and transport cannot.
        config.server = self.config.server
        self.config = config
        caps = discover(config, self.isolation)
        response = self.client.post("/internal/v1/nodes/heartbeat", json={"capabilities": caps, "policy": {"mode": config.mode, "allowed_users": config.allowed_users, "max_cpu_load": config.max_cpu_load, "max_memory_load": config.max_memory_load, "max_concurrency": 1}, "services": self.services.reports()})
        response.raise_for_status()
        self.services.config = config
        self.services.sync(response.json().get("services", []))
        return response.json()

    def run(self, once=False):
        backoff = 1
        while True:
            try:
                self.heartbeat()
                self.services.tick()
                backoff = 1
                if once:
                    return
                journal = self.config.work_root / "active-attempt.json"
                if journal.exists():
                    log.error("Unacknowledged attempt present: inspect worker termination before reconciliation")
                    time.sleep(self.config.heartbeat_seconds)
                    continue
                response = self.client.post("/internal/v1/jobs/claim", json={"claim_id": uuid.uuid4().hex, "wait_seconds": min(5, self.config.heartbeat_seconds)})
                response.raise_for_status()
                if response.status_code == 200:
                    from .execution import execute
                    execute(self, response.json())
            except (httpx.HTTPError, OSError, ValueError) as exc:
                log.warning("Connection unavailable (%s); local policy remains authoritative", type(exc).__name__)
                if once:
                    raise
                time.sleep(min(60, backoff) + random.random())
                backoff *= 2
