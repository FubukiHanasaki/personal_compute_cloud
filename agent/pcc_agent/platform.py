import json
import os
import platform
import shutil
import subprocess
import csv
import io
import re
import psutil


def gpu_metrics():
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        return [
            {
                "name": parts[0].strip(),
                "total_mb": int(parts[1]),
                "used_mb": int(parts[2]),
            }
            for line in result.stdout.splitlines()
            if len(parts := line.rsplit(",", 2)) == 3
        ]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def isolation_capabilities(config=None):
    if platform.system() == "Windows" and config is not None:
        from .windows_sandbox import probe

        return ["windows-appcontainer-job"] if probe(config) else []
    # Linux bubblewrap must actually work with this host's namespace policy.
    executable = shutil.which("bwrap") if platform.system() == "Linux" else None
    if executable:
        try:
            subprocess.run(
                [
                    executable,
                    "--unshare-all",
                    "--die-with-parent",
                    "--ro-bind",
                    "/",
                    "/",
                    "--",
                    "/bin/true",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=True,
            )
            return ["linux-bwrap-rlimit"]
        except (OSError, subprocess.SubprocessError):
            pass
    return []


def managed_isolation_available():
    if platform.system() != "Linux" or not shutil.which("systemd-run"):
        return False
    try:
        subprocess.run(
            [
                "systemd-run",
                "--user",
                "--scope",
                "--quiet",
                "-p",
                "MemoryMax=128M",
                "-p",
                "CPUQuota=100%",
                "-p",
                "TasksMax=16",
                "/usr/bin/true",
            ],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def discover(config, isolation=None):
    vm = psutil.virtual_memory()
    gpus = gpu_metrics()
    total_vram = max((g["total_mb"] for g in gpus), default=0)
    available_vram = max((g["total_mb"] - g["used_mb"] for g in gpus), default=0)
    physical = {
        "cpu": psutil.cpu_count() or 1,
        "memory_mb": vm.total // 2**20,
        "vram_mb": total_vram,
    }
    allocatable = {
        "cpu": min(physical["cpu"], config.allocatable_cpu),
        "memory_mb": min(physical["memory_mb"], config.allocatable_memory_mb),
        "vram_mb": min(total_vram, config.allocatable_vram_mb),
    }
    isolation = isolation if isolation is not None else isolation_capabilities(config)
    workers = []
    if config.enable_compute and isolation:
        workers.append("compute.stats")
    if config.enable_synthetic_ai and isolation:
        workers.extend(["synthetic.chat", "synthetic.image"])
    if (
        config.ollama_executable
        and config.ollama_models
        and managed_isolation_available()
    ):
        workers.extend("ollama:" + m for m in config.ollama_models)
    if (
        config.comfyui_python
        and config.comfyui_script
        and config.comfyui_workflows
        and managed_isolation_available()
    ):
        workers.extend("comfyui:" + name for name in config.comfyui_workflows)
    return {
        "physical": physical,
        "allocatable": allocatable,
        "available": {
            "cpu": physical["cpu"],
            "memory_mb": vm.available // 2**20,
            "vram_mb": available_vram,
        },
        "workers": workers,
        "gpu": gpus,
        "cpu_load": psutil.cpu_percent(),
        "memory_load": vm.percent,
        "disk_free_mb": shutil.disk_usage(config.work_root).free // 2**20,
        "isolation": isolation,
    }


def secure_windows_path(path):
    if os.name != "nt":
        return
    from pathlib import Path

    system = Path(os.environ["SystemRoot"]) / "System32"
    identity = subprocess.run(
        [str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    sid = next(csv.reader(io.StringIO(identity.stdout)))[-1]
    if not re.fullmatch(r"S-1-[0-9-]+", sid):
        raise RuntimeError("Unable to determine runtime account SID")
    permission = "(OI)(CI)F" if path.is_dir() else "F"
    subprocess.run(
        [
            str(system / "icacls.exe"),
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:{permission}",
            "/Q",
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )


def save_credential(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL prevents overwriting an enrolled identity or following a symlink.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as file:
        # Apply the DACL before writing any credential bytes on Windows.
        secure_windows_path(path)
        json.dump(data, file)
