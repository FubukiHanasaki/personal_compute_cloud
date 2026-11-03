from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Resources(Strict):
    cpu: float = Field(ge=0, le=65536)
    memory_mb: int = Field(ge=0, le=2**40)
    vram_mb: int = Field(default=0, ge=0, le=2**40)


class Capabilities(Strict):
    physical: Resources
    allocatable: Resources
    available: Resources
    workers: list[str] = Field(default_factory=list, max_length=32)
    gpu: list[dict] = Field(default_factory=list, max_length=32)
    cpu_load: float = Field(default=0, ge=0, le=100)
    memory_load: float = Field(default=0, ge=0, le=100)
    disk_free_mb: int = Field(default=0, ge=0)
    isolation: list[str] = Field(default_factory=list, max_length=16)


class Policy(Strict):
    mode: Literal["shared", "private", "reserved", "maintenance", "offline"] = (
        "maintenance"
    )
    allowed_users: list[str] = Field(default_factory=list, max_length=100)
    max_cpu_load: float = Field(default=85, ge=1, le=100)
    max_memory_load: float = Field(default=90, ge=1, le=100)
    max_concurrency: int = Field(default=1, ge=1, le=32)


class Register(Strict):
    token: str = Field(max_length=256)
    name: str = Field(min_length=1, max_length=80)
    os: Literal["Linux", "Windows"]


class ServiceReport(Strict):
    definition_id: str = Field(max_length=80)
    state: Literal["stopped", "starting", "ready", "idle", "stopping", "error"]
    active_requests: int = Field(ge=0, le=32)
    last_active: float = Field(ge=0)
    error: str | None = Field(default=None, max_length=300)


class Heartbeat(Strict):
    capabilities: Capabilities
    policy: Policy
    services: list[ServiceReport] = Field(default_factory=list, max_length=32)


class IssueToken(Strict):
    expires_seconds: int = Field(default=600, ge=30, le=3600)
