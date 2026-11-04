"""Locally approved NInfer managed server. Author: FubukiHanasaki."""

from pathlib import Path
import time
import httpx
from .services import OllamaBackend, check_loopback_port
from .managed_process import environment
from .inference import parameters, validate_messages


class NInferBackend(OllamaBackend):
    def __init__(self, config, model, resources):
        super().__init__(config, model, resources)
        self.port = config.ninfer_port
        self.client.close()
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}", timeout=120, trust_env=False
        )
        self.backend_model = model

    def command(self):
        p = self.parameters
        command = [
            self.config.ninfer_executable,
            str(self.config.ninfer_models[self.model]),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--device",
            "0",
            "--max-context",
            str(p["context_length"]),
            "--kv-dtype",
            p["kv_cache_type"],
            "--max-concurrency",
            "1",
            "--host-cache-capacity",
            "0B",
            "--context-capacity",
            f"{p['kv_capacity_mb']}MiB" if p["kv_capacity_mb"] else "auto",
            "--prefix-reuse" if p["prefix_reuse"] else "--no-prefix-reuse",
        ]
        if p["vision"]:
            command.append("--vision")
        return command

    def start(self):
        from .platform import managed_isolation_available

        executable = Path(self.config.ninfer_executable)
        artifact = self.config.ninfer_models.get(self.model)
        if (
            not managed_isolation_available()
            or not executable.is_absolute()
            or not executable.is_file()
            or not artifact
            or not artifact.is_absolute()
            or not artifact.is_file()
        ):
            raise RuntimeError(
                "NInfer runtime/artifact must be locally approved with enforceable process limits"
            )
        if (
            not self.config.ninfer_gpu_device
            or not 0 < self.config.ninfer_vram_mb <= self.resources["vram_mb"]
        ):
            raise RuntimeError(
                "NInfer requires an explicit CUDA UUID and approved VRAM reservation"
            )
        with self.lock:
            if self.cancelled.is_set():
                raise RuntimeError("Backend was stopped")
            check_loopback_port(self.port)
            self.logfile = (self.config.work_root / "ninfer.log").open("ab")
            env = {
                **environment(self.config, executable),
                "CUDA_VISIBLE_DEVICES": self.config.ninfer_gpu_device,
                "NINFER_VRAM_BUDGET_MIB": str(self.config.ninfer_vram_mb),
            }
            self.process = self.controller.start(
                self.command(), env=env, stdout=self.logfile
            )
        until = time.monotonic() + 120
        while time.monotonic() < until:
            if self.cancelled.is_set() or self.process.poll() is not None:
                raise RuntimeError("NInfer stopped during startup")
            if self.controller.listener_ready(self.port):
                try:
                    response = self._json("GET", "/v1/models", timeout=2)
                    models = response.get("data", [])
                    if len(models) == 1 and isinstance(models[0].get("id"), str):
                        self.backend_model = models[0]["id"]
                        return
                except httpx.HTTPError:
                    pass
            self.cancelled.wait(0.2)
        raise RuntimeError("NInfer model health check timed out")

    def chat(self, messages, max_bytes=2**20, timeout_seconds=120, overrides=None):
        p = parameters({**self.parameters, **(overrides or {})}, "ninfer")
        validate_messages(messages, p["vision"])
        response = self._json(
            "POST",
            "/v1/chat/completions",
            timeout=timeout_seconds,
            max_bytes=min(max_bytes * 6 + 65536, 20 * 2**20),
            json={
                "model": self.backend_model,
                "messages": messages,
                "stream": False,
                "max_tokens": p["max_tokens"],
                "temperature": p["temperature"],
                "top_p": p["top_p"],
                "top_k": p["top_k"],
                "seed": p["seed"],
                "chat_template_kwargs": {"enable_thinking": p["thinking"]},
            },
        )
        content = response["choices"][0]["message"]["content"]
        if not isinstance(content, str) or len(content.encode()) > max_bytes:
            raise RuntimeError("NInfer response exceeds output limit")
        return {"result": {"content": content}, "artifacts": []}

    def unload(self):
        # NInfer has no Ollama unload endpoint. Managed process stop releases weights.
        pass
