"""Separate managed process limits from physical startup RAM. Author: FubukiHanasaki."""

from typing import Annotated
from pydantic import Field, StringConstraints

MANAGED_PREFIXES = ("ollama:", "comfyui:", "ninfer:")
ManagedWorker = Annotated[
    str,
    StringConstraints(
        pattern=r"^(ollama|comfyui|ninfer):[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
        max_length=120,
    ),
]
ManagedThreshold = Annotated[int, Field(strict=True, ge=1024, le=1048576)]
ManagedBudget = Annotated[int, Field(strict=True, ge=0, le=1048576)]


def is_managed(worker):
    return worker.startswith(MANAGED_PREFIXES)


def validate_memory_config(workers, budget, thresholds):
    managed = [worker for worker in workers if is_managed(worker)]
    if (managed or thresholds) and budget < 1024:
        raise ValueError("受管推理必须配置至少 1024 MiB 的独立内存预算")
    if any(worker not in thresholds for worker in managed):
        raise ValueError("每个受管推理 worker 必须配置物理空闲内存启动门槛")


def resource_block_reason(caps, worker, requirements, used=None, reclaimable=None):
    used, reclaimable = used or {}, reclaimable or {}
    managed = is_managed(worker)
    if managed and (
        caps.get("managed_commit_budget_mb", 0) < 1024
        or worker not in caps.get("managed_available_memory_mb", {})
    ):
        return "受管推理内存预算或启动门槛未配置"
    for key, value in requirements.items():
        if key not in {"cpu", "memory_mb", "vram_mb"}:
            continue
        capacity = min(
            caps.get("allocatable", {}).get(key, 0),
            caps.get("physical", {}).get(key, 0),
        )
        physical_required = value
        available = min(
            caps.get("physical", {}).get(key, 0),
            caps.get("available", {}).get(key, 0) + reclaimable.get(key, 0),
        )
        if managed and key == "memory_mb":
            capacity = caps["managed_commit_budget_mb"]
            physical_required = caps["managed_available_memory_mb"][worker]
            # A commit reservation is not reclaimable resident RAM, even for an idle model.
            available = caps.get("available", {}).get(key, 0)
        label = {"cpu": "CPU", "memory_mb": "内存", "vram_mb": "显存"}[key]
        if capacity - used.get(key, 0) < value:
            return (
                "受管推理提交预算不足"
                if managed and key == "memory_mb"
                else f"{label}分配额度不足"
            )
        if available < physical_required:
            return (
                "物理空闲内存低于模型启动门槛"
                if managed and key == "memory_mb"
                else f"{label}可用资源不足"
            )
    return None
