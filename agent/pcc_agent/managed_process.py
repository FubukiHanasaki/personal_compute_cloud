"""Platform process-tree ownership for approved model servers. Author: Fubuki."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
from .windows_managed import JOB_PREFIX


def environment(config, executable):
    if os.name == "nt":
        root = Path(os.environ["SystemRoot"])
        home = config.work_root / "model-home"
        for name in ("tmp", "appdata", "localappdata"):
            (home / name).mkdir(parents=True, exist_ok=True)
        return {
            "SystemRoot": str(root),
            "WINDIR": str(root),
            "PATH": os.pathsep.join(
                (str(Path(executable).parent), str(root / "System32"), str(root))
            ),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(home / "appdata"),
            "LOCALAPPDATA": str(home / "localappdata"),
            "TEMP": str(home / "tmp"),
            "TMP": str(home / "tmp"),
        }
    return {
        "XDG_RUNTIME_DIR": os.environ.get(
            "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
        ),
        "DBUS_SESSION_BUS_ADDRESS": os.environ.get(
            "DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus"
        ),
        "PATH": "/usr/bin:/bin",
        "HOME": str(config.work_root),
    }


class ManagedProcess:
    def __init__(self, resources, unit):
        self.resources, self.unit = resources, unit
        self.process = None
        self.job_name = (
            JOB_PREFIX + unit.removeprefix("pcc-worker-") if os.name == "nt" else None
        )

    def start(self, command, env, stdout, cwd=None):
        if os.name == "nt":
            from .windows_managed import WindowsManagedProcess

            # Retain the controller before spawn, including partial failures.
            self.process = WindowsManagedProcess(self.resources, self.job_name)
            return self.process.start(command, env, stdout, cwd)
        if os.name != "posix":
            raise RuntimeError("Unsupported managed process platform")
        wrapper = [
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
            *command,
        ]
        self.process = subprocess.Popen(
            wrapper,
            stdout=stdout,
            stderr=stdout,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
        return self.process

    def stop(self):
        if self.process is None:
            return
        if self.job_name:
            self.process.close()
            return
        subprocess.run(
            ["systemctl", "--user", "kill", "--signal=KILL", self.unit + ".scope"],
            capture_output=True,
            timeout=10,
        )
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait(timeout=10)
        result = subprocess.run(
            ["systemctl", "--user", "stop", self.unit + ".scope"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode not in {0, 5}:
            raise RuntimeError("Cannot confirm managed scope has stopped")

    def listener_ready(self, port):
        if not self.job_name:
            return True
        return (
            self.process is not None
            and self.process.poll() is None
            and self.process.owns_listener(port)
        )

    def recovery_record(self):
        return (
            {"managed_job": self.job_name}
            if self.job_name
            else {"managed_scope": self.unit}
        )
