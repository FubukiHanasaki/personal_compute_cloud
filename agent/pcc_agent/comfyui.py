"""Approved local ComfyUI workflows; no user-uploaded graphs. Author: Fubuki."""

import base64
import copy
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
import tempfile
import uuid
import httpx
from .platform import managed_isolation_available
from .services import OllamaBackend

ALLOWED_NODES = {
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "EmptyLatentImage",
    "KSampler",
    "VAEDecode",
    "SaveImage",
}


def approved_workflow(config, model, data):
    approved = config.comfyui_workflows.get(model)
    if not approved or approved.file.stat().st_size > 1024 * 1024:
        raise RuntimeError("Local workflow is not approved")
    graph = json.loads(approved.file.read_text(encoding="utf-8"))
    if not isinstance(graph, dict) or not 1 <= len(graph) <= 32:
        raise RuntimeError("Invalid workflow graph")
    for key, node in graph.items():
        if (
            not isinstance(node, dict)
            or node.get("class_type") not in ALLOWED_NODES
            or not isinstance(node.get("inputs"), dict)
        ):
            raise RuntimeError("Workflow contains an unapproved node")
    if (
        graph.get(approved.prompt_node, {}).get("class_type") != "CLIPTextEncode"
        or graph.get(approved.latent_node, {}).get("class_type") != "EmptyLatentImage"
    ):
        raise RuntimeError(
            "Prompt/size bindings must target approved text and latent nodes"
        )
    if (
        set(data) != {"prompt", "size"}
        or data["size"] not in {"256x256", "512x512"}
        or not isinstance(data["prompt"], str)
        or not 1 <= len(data["prompt"]) <= 2000
    ):
        raise RuntimeError("Invalid generation parameters")
    graph = copy.deepcopy(graph)
    graph[approved.prompt_node]["inputs"]["text"] = data["prompt"]
    size = int(data["size"].split("x")[0])
    graph[approved.latent_node]["inputs"].update(width=size, height=size, batch_size=1)
    for node in graph.values():
        if node["class_type"] == "SaveImage":
            node["inputs"]["filename_prefix"] = "pcc-" + uuid.uuid4().hex
    return graph


class ComfyUIBackend(OllamaBackend):
    def __init__(self, config, model, resources):
        super().__init__(config, model, resources)
        self.staging = tempfile.TemporaryDirectory(
            prefix="comfyui-", dir=config.work_root
        )
        self.client.close()
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{config.comfyui_port}",
            timeout=5,
            trust_env=False,
        )

    def start(self):
        if not managed_isolation_available():
            raise RuntimeError("ComfyUI requires enforceable Linux user cgroup limits")
        approved = self.config.comfyui_workflows.get(self.model)
        executable, script = (
            Path(self.config.comfyui_python),
            Path(self.config.comfyui_script),
        )
        if (
            not approved
            or not executable.is_absolute()
            or not script.is_absolute()
            or not executable.is_file()
            or not script.is_file()
        ):
            raise RuntimeError("Local ComfyUI runtime/workflow is not approved")
        approved_workflow(
            self.config, self.model, {"prompt": "validation", "size": "256x256"}
        )
        if (
            self.config.comfyui_gpu_device
            and self.resources["vram_mb"] < approved.min_vram_mb
        ):
            raise RuntimeError("Workflow VRAM reservation is below local minimum")
        with self.lock:
            if self.cancelled.is_set():
                raise RuntimeError("Backend was stopped")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", self.config.comfyui_port))
            self.logfile = (self.config.work_root / "comfyui.log").open("ab")
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(self.config.work_root),
                "XDG_RUNTIME_DIR": os.environ.get(
                    "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
                ),
                "DBUS_SESSION_BUS_ADDRESS": os.environ.get(
                    "DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus"
                ),
                "CUDA_VISIBLE_DEVICES": self.config.comfyui_gpu_device,
                "HIP_VISIBLE_DEVICES": "",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            command = [
                shutil.which("systemd-run"),
                "--user",
                "--scope",
                "--quiet",
                "--unit",
                self.unit,
                "-p",
                f"MemoryMax={self.resources['memory_mb']}M",
                "-p",
                f"CPUQuota={int(self.resources['cpu'] * 100)}%",
                "-p",
                "TasksMax=256",
                str(executable),
                str(script),
                "--listen",
                "127.0.0.1",
                "--port",
                str(self.config.comfyui_port),
                "--disable-auto-launch",
                "--disable-all-custom-nodes",
                "--output-directory",
                str(Path(self.staging.name) / "output"),
                "--temp-directory",
                str(Path(self.staging.name) / "temp"),
            ]
            if not self.config.comfyui_gpu_device:
                command.append("--cpu")
            self.process = subprocess.Popen(
                command,
                cwd=script.parent,
                env=env,
                stdout=self.logfile,
                stderr=self.logfile,
                start_new_session=True,
            )
        until = time.monotonic() + 60
        while time.monotonic() < until:
            if self.cancelled.is_set() or self.process.poll() is not None:
                raise RuntimeError("ComfyUI stopped during startup")
            try:
                self.check_vram()
                return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("ComfyUI health check timed out")

    def check_vram(self):
        response = self.client.get("/system_stats")
        response.raise_for_status()
        used = sum(
            int(device.get("torch_vram_total", 0))
            for device in response.json().get("devices", [])
            if device.get("type") == "cuda"
        )
        if used > self.resources["vram_mb"] * 2**20:
            self.stop()
            raise RuntimeError("ComfyUI exceeded its approved VRAM reservation")

    def generate(self, data, max_bytes):
        graph = approved_workflow(self.config, self.model, data)
        response = self.client.post(
            "/prompt", json={"prompt": graph, "client_id": uuid.uuid4().hex}
        )
        response.raise_for_status()
        prompt_id = response.json()["prompt_id"]
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", prompt_id):
            raise RuntimeError("Invalid ComfyUI prompt identity")
        while not self.cancelled.is_set():
            response = self.client.get("/history/" + prompt_id)
            response.raise_for_status()
            history = response.json().get(prompt_id)
            if history:
                if history.get("status", {}).get("status_str") == "error":
                    raise RuntimeError("Approved image workflow failed")
                for output in history.get("outputs", {}).values():
                    for image in output.get("images", []):
                        name = image.get("filename", "")
                        subfolder = image.get("subfolder", "")
                        if (
                            not re.fullmatch(r"[a-zA-Z0-9_.-]+\.png", name)
                            or (
                                subfolder
                                and not re.fullmatch(
                                    r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", subfolder
                                )
                            )
                            or image.get("type") != "output"
                        ):
                            raise RuntimeError(
                                "Backend returned an unsafe artifact path"
                            )
                        chunks, length = [], 0
                        with self.client.stream(
                            "GET",
                            "/view",
                            params={
                                "filename": name,
                                "subfolder": subfolder,
                                "type": "output",
                            },
                        ) as result:
                            result.raise_for_status()
                            for chunk in result.iter_bytes():
                                length += len(chunk)
                                if length > max_bytes:
                                    raise RuntimeError(
                                        "Generated image exceeds artifact limit"
                                    )
                                chunks.append(chunk)
                        png = b"".join(chunks)
                        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                            raise RuntimeError("Backend result is not a PNG")
                        self.check_vram()
                        return {
                            "result": {
                                "model": self.model,
                                "size": data["size"],
                                "synthetic": False,
                            },
                            "artifacts": [
                                {
                                    "name": "image.png",
                                    "data_base64": base64.b64encode(png).decode(),
                                }
                            ],
                        }
                if history.get("status", {}).get("completed"):
                    raise RuntimeError("Workflow completed without an image")
            self.cancelled.wait(0.2)
        raise RuntimeError("Image request was stopped")

    def unload(self):
        if self.process and self.process.poll() is None:
            response = self.client.post(
                "/free", json={"unload_models": True, "free_memory": True}
            )
            response.raise_for_status()

    def stop(self):
        super().stop()
        self.staging.cleanup()
