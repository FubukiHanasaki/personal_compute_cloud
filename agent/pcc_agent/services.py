"""Managed inference lifecycle. No user-supplied executables. Author: Fubuki."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
import shutil
import uuid
import socket
import subprocess
import threading
import time
import httpx


class OllamaBackend:
    def __init__(self, config, model, resources):
        self.config, self.model, self.resources = config, model, resources
        self.process = None
        self.cancelled = threading.Event()
        self.lock = threading.RLock()
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{config.ollama_port}", timeout=httpx.Timeout(120, connect=3), trust_env=False)
        self.logfile = None
        self.unit = "pcc-worker-"+uuid.uuid4().hex

    def start(self):
        if os.name != "posix":
            raise RuntimeError("Managed Ollama currently requires Linux resource isolation; Windows refuses execution")
        from .platform import managed_isolation_available
        if not managed_isolation_available():
            raise RuntimeError("User cgroup CPU/memory limits are unavailable; inference refused")
        executable = Path(self.config.ollama_executable)
        if not executable.is_absolute() or not executable.is_file() or self.model not in self.config.ollama_models:
            raise RuntimeError("Local executable/model is not approved")
        with self.lock:
            if self.cancelled.is_set():
                raise RuntimeError("Backend was stopped")
            # Never adopt or stop an unrelated local server.
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", self.config.ollama_port))
            self.config.ollama_model_root.mkdir(parents=True, exist_ok=True)
            self.logfile = (self.config.work_root/"ollama.log").open("ab")
            env = {"XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus"), "PATH": "/usr/bin:/bin", "HOME": str(self.config.work_root), "OLLAMA_HOST": f"127.0.0.1:{self.config.ollama_port}", "OLLAMA_MODELS": str(self.config.ollama_model_root), "OLLAMA_KEEP_ALIVE": "-1", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_NO_CLOUD": "1"}
            command = [shutil.which("systemd-run"), "--user", "--scope", "--quiet", "--unit", self.unit, "-p", f"MemoryMax={self.resources['memory_mb']}M", "-p", f"CPUQuota={int(self.resources['cpu']*100)}%", "-p", "TasksMax=32", "-p", "LimitFSIZE=16777216", str(executable), "serve"]
            self.process = subprocess.Popen(command, stdout=self.logfile, stderr=self.logfile, env=env, start_new_session=True)
        until = time.monotonic()+20
        while time.monotonic()<until:
            if self.cancelled.is_set() or self.process.poll() is not None:
                raise RuntimeError("Managed backend stopped during startup")
            try:
                response = self.client.get("/api/version", timeout=1)
                response.raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Managed backend health check timed out")
        # Explicit load also verifies that the administrator has pre-installed the model.
        response = self.client.post("/api/generate", json={"model": self.model, "prompt": "", "stream": False, "keep_alive": -1})
        response.raise_for_status()

    def chat(self, messages):
        if self.cancelled.is_set():
            raise RuntimeError("Backend stopped")
        response = self.client.post("/api/chat", json={"model": self.model, "messages": messages, "stream": False, "keep_alive": -1})
        response.raise_for_status()
        return {"result": {"content": response.json()["message"]["content"]}, "artifacts": []}

    def unload(self):
        if self.process and self.process.poll() is None:
            response = self.client.post("/api/generate", json={"model": self.model, "prompt": "", "stream": False, "keep_alive": 0}, timeout=10)
            response.raise_for_status()

    def stop(self):
        self.cancelled.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                subprocess.run(["systemctl", "--user", "kill", "--signal=KILL", self.unit+".scope"], capture_output=True, timeout=10)
                if self.process.poll() is None:
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=10)
            if self.logfile:
                self.logfile.close()
                self.logfile = None


@dataclass
class Service:
    definition_id: str
    config: dict
    idle_seconds: int
    desired_state: str = "auto"
    state: str = "stopped"
    active_requests: int = 0
    last_active: float = field(default_factory=time.time)
    error: str | None = None
    backend: object = None


class ServiceManager:
    def __init__(self, config, factory=OllamaBackend, clock=time.time):
        self.config, self.factory, self.clock = config, factory, clock
        self.services = {}
        self.lock = threading.RLock()

    def sync(self, commands):
        with self.lock:
            for command in commands:
                definition = command["definition_id"]
                if definition not in self.services:
                    self.services[definition] = Service(definition, command["config"], min(command["idle_seconds"], self.config.service_idle_seconds), command["desired_state"])
                else:
                    if self.services[definition].desired_state != command["desired_state"]:
                        self.services[definition].last_active = self.clock()
                    self.services[definition].desired_state = command["desired_state"]

    def reports(self):
        with self.lock:
            return [{"definition_id": s.definition_id, "state": s.state, "active_requests": s.active_requests, "last_active": s.last_active, "error": s.error} for s in self.services.values()]

    def acquire(self, definition_id):
        with self.lock:
            service = self.services[definition_id]
            if service.desired_state == "stopped" or service.active_requests:
                raise RuntimeError("Service is draining or busy")
            if service.backend is None and service.config["worker"].startswith("ollama:"):
                requested = service.config["resources"]
                if requested["vram_mb"]>self.config.allocatable_vram_mb or requested["memory_mb"]>self.config.allocatable_memory_mb or requested["cpu"]>self.config.allocatable_cpu:
                    raise RuntimeError("Service exceeds local allocatable resources")
                # One managed Ollama server owns the configured loopback port/GPU lock.
                others = [s for s in self.services.values() if s is not service and s.backend]
                if any(s.active_requests for s in others):
                    raise RuntimeError("GPU resource lock is busy")
                for other in others:
                    self._stop(other)
                service.backend = self.factory(self.config, service.config["model"], requested)
                service.state = "starting"
            service.active_requests += 1
            service.last_active = self.clock()
            return service

    def ready(self, service):
        if service.state == "starting":
            service.backend.start()
        with self.lock:
            service.state = "ready"
            service.error = None

    def release(self, service, error=None):
        with self.lock:
            service.active_requests = max(0, service.active_requests-1)
            service.last_active = self.clock()
            service.state = "error" if error else "idle"
            service.error = type(error).__name__ if error else None
            if error:
                self._stop(service)
                service.state = "error"

    def _stop(self, service):
        if service.active_requests:
            return False
        service.state = "stopping"
        try:
            if service.backend:
                try:
                    service.backend.unload()
                finally:
                    service.backend.stop()
        except Exception as exc:
            service.error = type(exc).__name__
        service.backend = None
        service.state = "stopped"
        return True

    def tick(self):
        with self.lock:
            for service in self.services.values():
                idle = self.clock()-service.last_active >= service.idle_seconds
                if not service.active_requests and (service.desired_state == "stopped" or idle):
                    self._stop(service)
                elif service.desired_state == "ready" and service.state == "stopped" and not idle and self.config.mode not in {"maintenance", "offline"}:
                    # Manual warm-up is processed separately from heartbeat by Agent.
                    try:
                        self.acquire(service.definition_id)
                        self.ready(service)
                        self.release(service)
                    except Exception as exc:
                        self.release(service, exc)

    def stop_all(self):
        with self.lock:
            for service in self.services.values():
                if service.backend:
                    service.backend.stop()
                service.backend = None
                service.state = "stopped"


class InferenceWorker:
    def __init__(self, manager, payload):
        self.manager = manager
        self.service = manager.acquire(payload["template"]["id"])
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = self.executor.submit(self._run, payload)

    def _run(self, payload):
        error = None
        try:
            self.manager.ready(self.service)
            return self.service.backend.chat(payload["input"]["messages"])
        except Exception as exc:
            error = exc
            raise
        finally:
            self.manager.release(self.service, error)

    def poll(self):
        return 0 if self.future.done() else None

    def result(self):
        return self.future.result()

    def stop(self):
        if not self.future.done() and self.service.backend:
            self.service.backend.stop()
        try:
            self.future.result(timeout=15)
        except Exception:
            pass
        if not self.future.done():
            raise RuntimeError("Cannot confirm inference has stopped")

    def close(self):
        self.stop()
        self.executor.shutdown(wait=True)
