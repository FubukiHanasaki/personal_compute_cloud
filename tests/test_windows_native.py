"""Native Windows evidence; skips are never counted as platform acceptance. FubukiHanasaki."""

import os
from pathlib import Path
import socket
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.windows_sandbox import WindowsWorker, probe, cleanup_stale_profiles

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Native Windows AppContainer required"
)


def config(tmp_path):
    return AgentConfig(
        server="https://example.com",
        name="windows",
        work_root=tmp_path,
        credential_file=tmp_path / "identity.json",
        windows_worker_runtime=Path(os.environ["PCC_WINDOWS_RUNTIME"]),
    )


def payload():
    return {
        "template": {"worker": "compute.stats"},
        "input": {"values": [2, 4, 6]},
        "limits": {
            "cpu": 1,
            "memory_mb": 256,
            "timeout_seconds": 10,
            "output_bytes": 2**20,
        },
    }


def test_native_appcontainer_compute_and_profile_cleanup(tmp_path):
    worker = WindowsWorker(config(tmp_path), payload())
    try:
        with pytest.raises(RuntimeError, match="still alive"):
            cleanup_stale_profiles(config(tmp_path))
        assert worker.result()["result"]["sum"] == 12
    finally:
        worker.close()
    assert not list(tmp_path.glob("win-job-*"))
    assert not list(tmp_path.glob("windows-profile-*.json"))
    assert probe(config(tmp_path))


def test_native_appcontainer_blocks_private_files_and_loopback(monkeypatch, tmp_path):
    import pcc_agent.windows_sandbox as sandbox

    source = tmp_path / "source"
    source.mkdir()
    secret = tmp_path / "private-secret"
    secret.write_text("private-test-only")
    # Explicitly prevent ALL APPLICATION PACKAGES or inherited broad ACLs.
    import subprocess

    sid = (
        subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
        .strip()
        .split(",")[-1]
        .strip('"')
    )
    subprocess.run(
        ["icacls", str(secret), "/inheritance:r", "/grant:r", f"*{sid}:F"],
        check=True,
        capture_output=True,
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        (source / "worker.py").write_text(f"""import json, socket
denied = []
try:
    open({str(secret)!r}).read()
except OSError:
    denied.append("file")
try:
    socket.create_connection(("127.0.0.1", {port}), timeout=1).close()
except OSError:
    denied.append("network")
print(json.dumps({{"result": denied}}))
""")
        monkeypatch.setattr(sandbox, "__file__", str(source / "sandbox.py"))
        worker = WindowsWorker(config(tmp_path), payload())
        try:
            assert worker.result()["result"] == ["file", "network"]
        finally:
            worker.close()


@pytest.mark.parametrize(
    "body",
    ["while True: pass", "print('x' * 1000000)", "a = bytearray(1024 * 1024 * 1024)"],
)
def test_native_job_timeout_memory_and_output_limits(monkeypatch, tmp_path, body):
    import pcc_agent.windows_sandbox as sandbox

    source = tmp_path / "source"
    source.mkdir()
    (source / "worker.py").write_text(body)
    monkeypatch.setattr(sandbox, "__file__", str(source / "sandbox.py"))
    request = payload()
    request["limits"].update(timeout_seconds=1, output_bytes=1024)
    worker = WindowsWorker(config(tmp_path), request)
    try:
        with pytest.raises((RuntimeError, ValueError)):
            worker.result()
        assert worker.poll() is not None
    finally:
        worker.close()
