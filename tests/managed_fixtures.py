"""Explicit managed-memory fixtures for lifecycle tests. Author: FubukiHanasaki."""


def configure_managed_memory(manager, workers):
    manager.config.managed_commit_budget_mb = max(
        1024, manager.config.allocatable_memory_mb
    )
    manager.config.managed_available_memory_mb = {worker: 1024 for worker in workers}
    manager.capabilities = {
        "physical": {"cpu": 16, "memory_mb": 8192, "vram_mb": 8192},
        "allocatable": {
            "cpu": manager.config.allocatable_cpu,
            "memory_mb": manager.config.allocatable_memory_mb,
            "vram_mb": manager.config.allocatable_vram_mb,
        },
        "available": {"cpu": 16, "memory_mb": 8192, "vram_mb": 8192},
        "cpu_load": 0,
        "memory_load": 0,
        "managed_commit_budget_mb": manager.config.managed_commit_budget_mb,
        "managed_available_memory_mb": dict(manager.config.managed_available_memory_mb),
    }
