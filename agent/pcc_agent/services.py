"""Managed inference lifecycle. No user-supplied executables. Author: FubukiHanasaki."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import uuid
import socket
import threading
import time
import httpx
from .managed_process import ManagedProcess, environment
from .errors import WorkerStopUnconfirmed


def check_loopback_port(port):
    with socket.socket() as probe:
        # A stopped HTTP server can leave TIME_WAIT sockets. Reusing those is
        # safe; a live listener still prevents this bind (no SO_REUSEPORT).
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


class OllamaBackend:
    def __init__(self, config, model, resources):
        self.config, self.model, self.resources = config, model, resources
        self.process = None
        self.port = config.ollama_port
        self.cancelled = threading.Event()
        self.lock = threading.RLock()
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{config.ollama_port}",
            timeout=httpx.Timeout(120, connect=3),
            trust_env=False,
        )
        self.logfile = None
        self.unit = "pcc-worker-" + uuid.uuid4().hex
        self.controller = ManagedProcess(resources, self.unit)

    def recovery_record(self):
        return self.controller.recovery_record()

    def _require_listener(self, port):
        if not self.controller.listener_ready(port):
            raise RuntimeError("Managed loopback listener is unavailable or not owned")

    def _read_http(
        self, method, path, max_bytes, *, timeout=5, deadline=None, **kwargs
    ):
        until = deadline if deadline is not None else time.monotonic() + timeout

        def remaining():
            if self.cancelled.is_set():
                raise RuntimeError("Backend request was stopped")
            seconds = until - time.monotonic()
            if seconds <= 0:
                raise RuntimeError("Backend request timed out")
            return seconds

        seconds = remaining()
        self._require_listener(self.port)
        with self.client.stream(
            method,
            path,
            timeout=min(timeout, seconds),
            headers={"Accept-Encoding": "identity"},
            **kwargs,
        ) as response:
            response.raise_for_status()
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise RuntimeError("Compressed backend responses are not accepted")
            content = bytearray()
            # A bytearray also bounds overhead for arbitrarily small chunks.
            for chunk in response.iter_bytes():
                remaining()
                if len(content) + len(chunk) > max_bytes:
                    raise RuntimeError("Backend response exceeds output limit")
                content.extend(chunk)
            remaining()
            return bytes(content)

    def _json(self, method, path, *, max_bytes=2**20, **kwargs):
        result = json.loads(self._read_http(method, path, max_bytes, **kwargs))
        if not isinstance(result, dict):
            raise RuntimeError("Invalid backend JSON response")
        return result

    def start(self):
        from .platform import managed_isolation_available

        if not managed_isolation_available():
            raise RuntimeError(
                "Managed process CPU/memory limits are unavailable; inference refused"
            )
        executable = Path(self.config.ollama_executable)
        if (
            not executable.is_absolute()
            or not executable.is_file()
            or self.model not in self.config.ollama_models
        ):
            raise RuntimeError("Local executable/model is not approved")
        if self.config.ollama_gpu_device and (
            self.config.ollama_vram_mb <= 0
            or self.resources["vram_mb"] < self.config.ollama_vram_mb
        ):
            raise RuntimeError(
                "GPU model requires a local VRAM reservation within template resources"
            )
        with self.lock:
            if self.cancelled.is_set():
                raise RuntimeError("Backend was stopped")
            # Never adopt or stop an unrelated local server.
            check_loopback_port(self.config.ollama_port)
            self.config.ollama_model_root.mkdir(parents=True, exist_ok=True)
            self.logfile = (self.config.work_root / "ollama.log").open("ab")
            env = {
                **environment(self.config, executable),
                "OLLAMA_HOST": f"127.0.0.1:{self.config.ollama_port}",
                "OLLAMA_MODELS": str(self.config.ollama_model_root),
                "OLLAMA_KEEP_ALIVE": "-1",
                "OLLAMA_NUM_PARALLEL": "1",
                "OLLAMA_MAX_LOADED_MODELS": "1",
                "OLLAMA_NO_CLOUD": "1",
                "CUDA_VISIBLE_DEVICES": self.config.ollama_gpu_device or "-1",
                "HIP_VISIBLE_DEVICES": "-1",
                "ROCR_VISIBLE_DEVICES": "-1",
                "OLLAMA_VULKAN": "0",
                "GGML_VK_VISIBLE_DEVICES": "-1",
            }
            self.process = self.controller.start(
                [str(executable), "serve"],
                env=env,
                stdout=self.logfile,
            )
        until = time.monotonic() + 20
        while time.monotonic() < until:
            if self.cancelled.is_set() or self.process.poll() is not None:
                raise RuntimeError("Managed backend stopped during startup")
            if not self.controller.listener_ready(self.config.ollama_port):
                time.sleep(0.1)
                continue
            try:
                self._json("GET", "/api/version", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Managed backend health check timed out")
        # Explicit load also verifies that the administrator has pre-installed the model.
        self._json(
            "POST",
            "/api/generate",
            timeout=120,
            json={"model": self.model, "prompt": "", "stream": False, "keep_alive": -1},
        )
        self.check_vram()

    def check_vram(self):
        response = self._json("GET", "/api/ps", timeout=3)
        used = sum(int(m.get("size_vram", 0)) for m in response.get("models", []))
        if self.config.ollama_gpu_device and used <= 0:
            self.stop()
            raise RuntimeError(
                "Configured Ollama GPU is not in use; CPU fallback refused"
            )
        if used > self.resources["vram_mb"] * 2**20:
            self.stop()
            raise RuntimeError("Managed model exceeded its approved VRAM reservation")

    def chat(self, messages, max_bytes=2**20, timeout_seconds=120):
        if self.cancelled.is_set():
            raise RuntimeError("Backend stopped")
        response = self._json(
            "POST",
            "/api/chat",
            timeout=min(120, timeout_seconds),
            max_bytes=min(max_bytes * 6 + 65536, 20 * 2**20),
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "keep_alive": -1,
            },
        )
        content = response["message"]["content"]
        if not isinstance(content, str) or len(content.encode("utf-8")) > max_bytes:
            raise RuntimeError("Chat response exceeds output limit")
        self.check_vram()
        return {
            "result": {"content": content},
            "artifacts": [],
        }

    def unload(self):
        if self.process and self.process.poll() is None:
            self._json(
                "POST",
                "/api/generate",
                json={
                    "model": self.model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": 0,
                },
                timeout=10,
            )

    def stop(self):
        self.cancelled.set()
        with self.lock:
            self.controller.stop()
            if self.logfile:
                self.logfile.close()
                self.logfile = None
            self.client.close()


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
        self.closed = False
        self.capabilities = {}

    def sync(self, commands):
        with self.lock:
            for command in commands:
                definition = command["definition_id"]
                if definition not in self.services:
                    self.services[definition] = Service(
                        definition,
                        command["config"],
                        min(command["idle_seconds"], self.config.service_idle_seconds),
                        command["desired_state"],
                    )
                else:
                    if (
                        self.services[definition].desired_state
                        != command["desired_state"]
                    ):
                        self.services[definition].last_active = self.clock()
                    self.services[definition].desired_state = command["desired_state"]

    def reports(self):
        with self.lock:
            return [
                {
                    "definition_id": s.definition_id,
                    "state": s.state,
                    "active_requests": s.active_requests,
                    "last_active": s.last_active,
                    "error": s.error,
                }
                for s in self.services.values()
            ]

    def acquire(self, definition_id):
        with self.lock:
            if self.closed:
                raise RuntimeError("Service manager was stopped")
            service = self.services[definition_id]
            if (
                service.desired_state == "stopped"
                or service.active_requests
                or (service.state == "error" and service.backend is not None)
            ):
                raise RuntimeError(
                    "Service is draining, busy or awaiting stop confirmation"
                )
            if service.config["worker"].startswith(("ollama:", "comfyui:")):
                requested = service.config["resources"]
                if self._exceeds_budget(service):
                    raise RuntimeError("Service exceeds local allocatable resources")
            if service.backend is None and service.config["worker"].startswith(
                ("ollama:", "comfyui:")
            ):
                # One managed Ollama server owns the configured loopback port/GPU lock.
                others = [
                    s for s in self.services.values() if s is not service and s.backend
                ]
                if any(s.active_requests for s in others):
                    raise RuntimeError("GPU resource lock is busy")
                for other in others:
                    if not self._stop(other):
                        raise RuntimeError(
                            "Previous service is awaiting stop confirmation"
                        )
                if service.config["worker"].startswith("comfyui:"):
                    from .comfyui import ComfyUIBackend

                    factory = ComfyUIBackend
                else:
                    factory = self.factory
                service.backend = factory(
                    self.config, service.config["model"], requested
                )
                service.state = "starting"
            service.active_requests += 1
            service.last_active = self.clock()
            return service

    def _exceeds_budget(self, service):
        return service.config["worker"].startswith(("ollama:", "comfyui:")) and any(
            service.config["resources"][key]
            > min(
                getattr(self.config, "allocatable_" + key),
                self.capabilities.get("allocatable", {}).get(
                    key, getattr(self.config, "allocatable_" + key)
                ),
            )
            for key in ("cpu", "memory_mb", "vram_mb")
        )

    def ready(self, service):
        with self.lock:
            if self.closed:
                raise RuntimeError("Service manager was stopped")
            backend, starting = service.backend, service.state == "starting"
        if starting:
            backend.start()
        with self.lock:
            if self.closed or service.backend is not backend:
                raise RuntimeError("Service was stopped during startup")
            service.state = "ready"
            service.error = None
            return backend

    def release(self, service, error=None):
        with self.lock:
            service.active_requests = max(0, service.active_requests - 1)
            service.last_active = self.clock()
            if self.closed:
                self._stop(service)
                return
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
                except Exception as exc:
                    service.error = type(exc).__name__
                service.backend.stop()
        except Exception as exc:
            service.error = type(exc).__name__
            service.state = "error"
            return False
        service.backend = None
        service.state = "stopped"
        return True

    def tick(self):
        with self.lock:
            if self.closed:
                return
            for service in self.services.values():
                idle = self.clock() - service.last_active >= service.idle_seconds
                if not service.active_requests and (
                    service.desired_state == "stopped"
                    or idle
                    or self._exceeds_budget(service)
                    or (service.state == "error" and service.backend is not None)
                ):
                    self._stop(service)
                elif (
                    service.desired_state == "ready"
                    and service.state == "stopped"
                    and not idle
                    and self.config.mode not in {"maintenance", "offline"}
                    and self.capabilities.get("cpu_load", 0) < self.config.max_cpu_load
                    and self.capabilities.get("memory_load", 0)
                    < self.config.max_memory_load
                ):
                    # Reserve synchronously, load outside the heartbeat thread/lock.
                    try:
                        self.acquire(service.definition_id)
                    except Exception as exc:
                        service.state = "error"
                        service.error = type(exc).__name__
                        continue
                    threading.Thread(
                        target=self._warm, args=(service,), daemon=True
                    ).start()

    def _warm(self, service):
        error = None
        try:
            self.ready(service)
        except Exception as exc:
            error = exc
        finally:
            self.release(service, error)

    def stop_all(self):
        with self.lock:
            self.closed = True
            failures = []
            for service in self.services.values():
                try:
                    if service.backend:
                        service.backend.stop()
                except Exception as exc:
                    service.state = "error"
                    service.error = type(exc).__name__
                    failures.append(exc)
                    continue
                service.backend = None
                service.state = "stopped"
            if failures:
                raise WorkerStopUnconfirmed(
                    "Managed service stop requires reconciliation"
                ) from failures[0]


class InferenceWorker:
    def __init__(self, manager, payload):
        self.manager = manager
        self.service = manager.acquire(payload["template"]["id"])
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = self.executor.submit(self._run, payload)

    def _run(self, payload):
        error = None
        try:
            backend = self.manager.ready(self.service)
            if payload["template"]["worker"].startswith("comfyui:"):
                return backend.generate(
                    payload["input"],
                    payload["limits"]["output_bytes"],
                    payload["limits"]["timeout_seconds"],
                )
            return backend.chat(
                payload["input"]["messages"],
                payload["limits"]["output_bytes"],
                payload["limits"]["timeout_seconds"],
            )
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
        if self.service.state == "error" and self.service.backend:
            self.service.backend.stop()

    def close(self):
        try:
            self.stop()
        finally:
            # A failed stop must preserve the backend/journal, but must not
            # leave an idle executor behind or wait forever for a live call.
            self.executor.shutdown(wait=self.future.done(), cancel_futures=True)
