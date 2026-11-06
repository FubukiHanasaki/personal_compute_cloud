"""Approved local ComfyUI workflows; no user-uploaded graphs. Author: FubukiHanasaki."""

import base64
import copy
import json
from pathlib import Path
import re
import shutil
import time
import tempfile
import uuid
import httpx
from .platform import managed_isolation_available
from .services import OllamaBackend, check_loopback_port
from .managed_process import environment
from .inference import (
    IMAGE_SIZES,
    image_bytes,
    ImageParameters,
    image_seed,
    validate_negative_prompt,
)

ALLOWED_NODES = {
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "EmptyLatentImage",
    "KSampler",
    "VAEDecode",
    "SaveImage",
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "TextEncodeQwenImage21",
    "QwenImage21Cache",
    "LoadImage",
}


def bind_negative_prompt(graph, approved, value):
    """Only mutate a locally approved conditioning input connected to KSampler."""
    validate_negative_prompt(value)
    if value is None:
        return
    node_id = approved.negative_prompt_node
    if (
        not node_id
        and graph[approved.prompt_node]["class_type"] == "TextEncodeQwenImage21"
    ):
        node_id = approved.prompt_node
    node = graph.get(node_id, {})
    kind = node.get("class_type")
    if kind not in {"CLIPTextEncode", "TextEncodeQwenImage21"}:
        raise RuntimeError("Negative prompt requires an approved text node binding")
    if kind == "CLIPTextEncode" and node_id == approved.prompt_node:
        raise RuntimeError("Positive and negative CLIP text nodes must be distinct")
    sampler = graph.get(approved.sampler_node, {})
    port = 1 if kind == "TextEncodeQwenImage21" else 0
    if (
        sampler.get("class_type") != "KSampler"
        or sampler["inputs"].get("negative") != [node_id, port]
        or sampler["inputs"].get("positive") != [approved.prompt_node, 0]
    ):
        raise RuntimeError(
            "Negative prompt binding must feed the approved KSampler negative input"
        )
    node["inputs"]["negative_prompt" if kind == "TextEncodeQwenImage21" else "text"] = (
        value
    )


def approved_workflow(config, model, data, image_parameters=None):
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
        graph.get(approved.prompt_node, {}).get("class_type")
        not in {"CLIPTextEncode", "TextEncodeQwenImage21"}
        or graph.get(approved.latent_node, {}).get("class_type") != "EmptyLatentImage"
    ):
        raise RuntimeError(
            "Prompt/size bindings must target approved text and latent nodes"
        )
    if (
        set(data) - {"prompt", "size", "images", "seed", "negative_prompt"}
        or not {"prompt", "size"} <= set(data)
        or not isinstance(data["size"], str)
        or data["size"] not in IMAGE_SIZES
        or not isinstance(data["prompt"], str)
        or not 1 <= len(data["prompt"]) <= 2000
    ):
        raise RuntimeError("Invalid generation parameters")
    graph = copy.deepcopy(graph)
    text_key = (
        "prompt"
        if graph[approved.prompt_node]["class_type"] == "TextEncodeQwenImage21"
        else "text"
    )
    graph[approved.prompt_node]["inputs"][text_key] = data["prompt"]
    bind_negative_prompt(graph, approved, data.get("negative_prompt"))
    width, height = map(int, data["size"].split("x"))
    graph[approved.latent_node]["inputs"].update(
        width=width, height=height, batch_size=1
    )
    settings = ImageParameters(**(image_parameters or {}))
    seed = image_seed(data.get("seed"), settings.seed)
    if data.get("seed") is not None and not approved.sampler_node:
        raise RuntimeError("Per-request seed requires an approved KSampler binding")
    if approved.sampler_node:
        if graph.get(approved.sampler_node, {}).get("class_type") != "KSampler":
            raise RuntimeError("Sampler binding must target KSampler")
        graph[approved.sampler_node]["inputs"].update(
            steps=settings.steps, cfg=settings.cfg, seed=seed
        )
    images = data.get("images", [])
    if not isinstance(images, list) or len(images) > len(approved.image_nodes):
        raise RuntimeError("Workflow does not support this many reference images")
    for node_id, node in list(graph.items()):
        if node["class_type"] == "LoadImage" and node_id not in approved.image_nodes:
            raise RuntimeError(
                "Every LoadImage must be explicitly bound to request data"
            )
    for index, node_id in enumerate(approved.image_nodes):
        if graph.get(node_id, {}).get("class_type") != "LoadImage":
            raise RuntimeError("Reference image binding must target LoadImage")
        if index < len(images):
            image_bytes(images[index])
            graph[node_id]["inputs"]["image"] = f"reference-{index}.png"
        else:
            del graph[node_id]
            for node in graph.values():
                node["inputs"] = {
                    key: value
                    for key, value in node["inputs"].items()
                    if not (isinstance(value, list) and value and value[0] == node_id)
                }
    for node in graph.values():
        if node["class_type"] == "SaveImage":
            node["inputs"]["filename_prefix"] = "pcc-" + uuid.uuid4().hex
    return graph


class ComfyUIBackend(OllamaBackend):
    def __init__(self, config, model, resources):
        super().__init__(config, model, resources)
        self.port = config.comfyui_port
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
            raise RuntimeError("ComfyUI requires enforceable managed process limits")
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
            check_loopback_port(self.config.comfyui_port)
            self.logfile = (self.config.work_root / "comfyui.log").open("ab")
            for name in ("user", "input"):
                (Path(self.staging.name) / name).mkdir()
            env = {
                **environment(self.config, executable),
                "CUDA_VISIBLE_DEVICES": self.config.comfyui_gpu_device or "-1",
                "HIP_VISIBLE_DEVICES": "-1",
                "ROCR_VISIBLE_DEVICES": "-1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
            command = [
                str(executable),
                "-I",
                "-B",
                "-X",
                "utf8",
                str(Path(__file__).with_name("managed_exec.py")),
                str(self.resources["vram_mb"]),
                str(script),
                "--listen",
                "127.0.0.1",
                "--port",
                str(self.config.comfyui_port),
                "--disable-auto-launch",
                "--disable-all-custom-nodes",
                "--disable-api-nodes",
                "--disable-metadata",
                # Custom DynamicVRAM allocators bypass Torch's memory fraction.
                "--disable-dynamic-vram",
                "--output-directory",
                str(Path(self.staging.name) / "output"),
                "--temp-directory",
                str(Path(self.staging.name) / "temp"),
                "--user-directory",
                str(Path(self.staging.name) / "user"),
                "--input-directory",
                str(Path(self.staging.name) / "input"),
            ]
            if not self.config.comfyui_gpu_device:
                command.append("--cpu")
            self.process = self.controller.start(
                command,
                cwd=script.parent,
                env=env,
                stdout=self.logfile,
            )
        until = time.monotonic() + 60
        while time.monotonic() < until:
            if self.cancelled.is_set() or self.process.poll() is not None:
                raise RuntimeError("ComfyUI stopped during startup")
            if not self.controller.listener_ready(self.config.comfyui_port):
                time.sleep(0.2)
                continue
            try:
                self.check_vram()
                return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("ComfyUI health check timed out")

    def check_vram(self):
        devices = self._json("GET", "/system_stats").get("devices", [])
        if self.config.comfyui_gpu_device and not any(
            device.get("type") == "cuda" for device in devices
        ):
            self.stop()
            raise RuntimeError("Configured ComfyUI CUDA device is unavailable")
        used = sum(
            int(device.get("torch_vram_total", 0))
            for device in devices
            if device.get("type") == "cuda"
        )
        if used > self.resources["vram_mb"] * 2**20:
            self.stop()
            raise RuntimeError("ComfyUI exceeded its approved VRAM reservation")

    def generate(self, data, max_bytes, timeout_seconds=300):
        self._require_listener(self.config.comfyui_port)
        until = time.monotonic() + max(1, timeout_seconds)
        graph = approved_workflow(self.config, self.model, data, self.image_parameters)
        sampler_node = self.config.comfyui_workflows[self.model].sampler_node
        used_seed = graph[sampler_node]["inputs"]["seed"] if sampler_node else None
        input_root = Path(self.staging.name) / "input"
        for index, url in enumerate(data.get("images", [])):
            path = input_root / f"reference-{index}.png"
            if path.is_symlink():
                raise RuntimeError("Unsafe input staging path")
            path.write_bytes(image_bytes(url))
        response = self._json(
            "POST",
            "/prompt",
            deadline=until,
            json={"prompt": graph, "client_id": uuid.uuid4().hex},
        )
        prompt_id = response["prompt_id"]
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", prompt_id):
            raise RuntimeError("Invalid ComfyUI prompt identity")
        while not self.cancelled.is_set():
            if time.monotonic() >= until:
                raise RuntimeError("Image generation timed out")
            response = self._json("GET", "/history/" + prompt_id, deadline=until)
            history = response.get(prompt_id)
            if history:
                if history.get("status", {}).get("status_str") == "error":
                    raise RuntimeError("Approved image workflow failed")
                # History can contain partial outputs while other nodes still
                # run. Do not release the service or remove files until done.
                if not history.get("status", {}).get("completed"):
                    self.cancelled.wait(0.2)
                    continue
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
                        png = self._read_http(
                            "GET",
                            "/view",
                            max_bytes,
                            deadline=until,
                            params={
                                "filename": name,
                                "subfolder": subfolder,
                                "type": "output",
                            },
                        )
                        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                            raise RuntimeError("Backend result is not a PNG")
                        self.check_vram()
                        self._cleanup_completed(prompt_id, until)
                        return {
                            "result": {
                                "model": self.model,
                                "size": data["size"],
                                "synthetic": False,
                                "seed": used_seed,
                                "negative_prompt": data.get("negative_prompt"),
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
            self._read_http(
                "POST",
                "/free",
                65536,
                json={"unload_models": True, "free_memory": True},
            )

    def _cleanup_completed(self, prompt_id, deadline):
        with self.lock:
            # Bound retained server history and local artifacts across a busy
            # service's lifetime, including extra outputs in approved graphs.
            self._read_http(
                "POST",
                "/history",
                65536,
                json={"delete": [prompt_id]},
                deadline=deadline,
            )
            root = Path(self.staging.name).resolve(strict=True)
            for name in ("output", "temp", "input"):
                path = root / name
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise RuntimeError("Unsafe managed staging directory")
                if path.exists():
                    shutil.rmtree(path)
                path.mkdir()

    def stop(self):
        with self.lock:
            super().stop()
            self.staging.cleanup()
