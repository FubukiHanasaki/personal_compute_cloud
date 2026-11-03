import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from .platform import isolation_capabilities


class SandboxedWorker:
    def __init__(self, config, payload):
        if "linux-bwrap-rlimit" not in isolation_capabilities():
            raise RuntimeError("Required isolation is unavailable; execution refused")
        if payload["template"]["worker"] not in {
            "compute.stats",
            "synthetic.chat",
            "synthetic.image",
        }:
            raise RuntimeError("Unapproved isolated worker")
        self.directory = tempfile.TemporaryDirectory(
            prefix="job-", dir=config.work_root
        )
        root = Path(self.directory.name)
        self.output = (root / "stdout").open("w+b")
        self.stderr = (root / "stderr").open("w+b")
        bwrap = shutil.which("bwrap")
        args = [
            bwrap,
            "--unshare-all",
            "--new-session",
            "--die-with-parent",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/work",
            "--chdir",
            "/work",
        ]
        for path in dict.fromkeys(
            ["/usr", "/bin", "/lib", "/lib64", str(Path(sys.base_prefix).resolve())]
        ):
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        args.extend(
            [
                "--ro-bind",
                str(Path(__file__).with_name("worker.py")),
                "/worker.py",
                "--",
                str(Path(sys.executable).resolve()),
                "-I",
                "-B",
                "/worker.py",
            ]
        )
        limits = payload["limits"]
        command = [
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).with_name("limit_exec.py")),
            str(limits["cpu"]),
            str(limits["memory_mb"]),
            str(limits["timeout_seconds"]),
            str(min(limits["output_bytes"] * 2, 20 * 2**20)),
            *args,
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=self.output,
            stderr=self.stderr,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        self.process.stdin.write(
            json.dumps(
                {"worker": payload["template"]["worker"], "input": payload["input"]}
            ).encode()
        )
        self.process.stdin.close()

    def poll(self):
        return self.process.poll()

    def stop(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=10)

    def result(self):
        if self.process.wait() != 0:
            raise RuntimeError("Isolated worker failed or exceeded resource limit")
        self.output.seek(0)
        return json.loads(self.output.read(20 * 2**20))

    def close(self):
        self.stop()
        self.output.close()
        self.stderr.close()
        self.directory.cleanup()
