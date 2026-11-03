"""Platform-independent validation of the Windows adapter contract. Fubuki."""

import ctypes
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.windows_sandbox import (
    BasicLimits,
    ExtendedLimits,
    StartupEx,
    runtime_root,
)


def test_windows_runtime_is_not_an_agent_or_credential_directory(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "python.exe").touch()
    (runtime / "python313.zip").touch()
    config = AgentConfig(
        server="https://example.com",
        name="test",
        windows_worker_runtime=runtime,
        work_root=tmp_path / "jobs",
        credential_file=runtime / "identity.json",
    )
    with pytest.raises(RuntimeError, match="credentials"):
        runtime_root(config)
    config.credential_file = tmp_path / "identity.json"
    assert runtime_root(config) == runtime
    (runtime / "private.json").write_text("{}")
    with pytest.raises(RuntimeError, match="vendor"):
        runtime_root(config)
    (runtime / "private.json").unlink()
    config.work_root = runtime / "jobs"
    with pytest.raises(RuntimeError):
        runtime_root(config)


def test_win64_job_and_startup_abi_sizes():
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("Win64 layout test")
    assert ctypes.sizeof(BasicLimits) == 64
    assert ctypes.sizeof(ExtendedLimits) == 144
    assert ctypes.sizeof(StartupEx) == 112


def test_windows_scheduler_accepts_approved_isolation_but_not_worker_name_alone(env):
    from conftest import register_node
    from test_jobs import template, submit, claim

    _, client, auth, _ = env
    template(client, auth)
    _, node, caps = register_node(client, auth)
    submit(client, auth)
    caps["isolation"] = []
    client.post(
        "/internal/v1/nodes/heartbeat",
        headers=node,
        json={"capabilities": caps, "policy": {"mode": "shared"}},
    )
    assert claim(client, node).status_code == 204
    caps["isolation"] = ["windows-appcontainer-job"]
    client.post(
        "/internal/v1/nodes/heartbeat",
        headers=node,
        json={"capabilities": caps, "policy": {"mode": "shared"}},
    )
    assert claim(client, node).status_code == 200
