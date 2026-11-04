from pathlib import Path
import tomllib
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, model_validator

GPU_DEVICE_PATTERN = r"^(?:GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})?$"


class ComfyWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: Path
    prompt_node: str
    latent_node: str
    min_vram_mb: int = Field(default=1024, ge=0)
    sampler_node: str = ""
    image_nodes: list[str] = Field(default_factory=list, max_length=4)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    server: str
    name: str = Field(min_length=1, max_length=80)
    credential_file: Path = Path("tmp/agent-credential.json")
    work_root: Path = Path("tmp/agent")
    mode: str = "maintenance"
    allowed_users: list[str] = []
    allocatable_cpu: float = Field(default=1, ge=0.1)
    allocatable_memory_mb: int = Field(default=512, ge=256)
    allocatable_vram_mb: int = Field(default=0, ge=0)
    max_cpu_load: float = Field(default=85, ge=1, le=100)
    max_memory_load: float = Field(default=90, ge=1, le=100)
    heartbeat_seconds: int = Field(default=10, ge=1, le=30)
    enable_compute: bool = False
    enable_synthetic_ai: bool = False
    allow_development_http: bool = False
    ca_file: Path | None = None
    windows_worker_runtime: Path | None = None
    # Administrator-selected local managed backend settings; never sent by a user.
    ollama_executable: str = ""
    ollama_models: list[str] = []
    ollama_model_root: Path = Path("tmp/ollama-models")
    ollama_port: int = Field(default=11435, ge=1024, le=65535)
    ollama_gpu_device: str = Field(default="", pattern=GPU_DEVICE_PATTERN)
    ollama_vram_mb: int = Field(default=0, ge=0)
    comfyui_python: str = ""
    comfyui_script: str = ""
    comfyui_port: int = Field(default=8189, ge=1024, le=65535)
    comfyui_gpu_device: str = Field(default="", pattern=GPU_DEVICE_PATTERN)
    comfyui_workflows: dict[str, ComfyWorkflow] = Field(default_factory=dict)
    ninfer_executable: str = ""
    ninfer_models: dict[str, Path] = Field(default_factory=dict, max_length=32)
    ninfer_port: int = Field(default=8089, ge=1024, le=65535)
    ninfer_gpu_device: str = Field(default="", pattern=GPU_DEVICE_PATTERN)
    ninfer_vram_mb: int = Field(default=0, ge=0)
    service_idle_seconds: int = Field(default=300, ge=1)

    @model_validator(mode="after")
    def validate_config(self):
        parsed = urlparse(self.server)
        if not parsed.hostname or (
            parsed.port is not None and not 1 <= parsed.port <= 65535
        ):
            raise ValueError("Server origin must include a valid hostname and port")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("Server must be a clean origin URL")
        if parsed.scheme != "https" and not (
            self.allow_development_http
            and parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("HTTPS required; development HTTP is limited to loopback")
        if self.version != 1 or self.mode not in {
            "shared",
            "private",
            "reserved",
            "maintenance",
            "offline",
        }:
            raise ValueError("Unsupported configuration version or mode")
        return self

    @classmethod
    def load(cls, path):
        with open(path, "rb") as file:
            config = cls(**tomllib.load(file))
        root = Path(path).resolve().parent
        for key in ("credential_file", "work_root", "ollama_model_root"):
            value = getattr(config, key)
            if not value.is_absolute():
                setattr(config, key, root / value)
        for key in ("ca_file", "windows_worker_runtime"):
            value = getattr(config, key)
            if value is not None and not value.is_absolute():
                setattr(config, key, root / value)
        config.ninfer_models = {
            key: value if value.is_absolute() else root / value
            for key, value in config.ninfer_models.items()
        }
        for workflow in config.comfyui_workflows.values():
            if not workflow.file.is_absolute():
                workflow.file = root / workflow.file
        return config
