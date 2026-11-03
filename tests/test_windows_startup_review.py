"""Windows startup diagnosis without relaxing isolation. FubukiHanasaki."""

from pathlib import Path
from types import SimpleNamespace
import logging
import pytest
from pcc_agent.windows_sandbox import (
    WindowsWorker,
    runtime_acl_args,
    worker_environment,
)


def test_worker_profile_environment_is_complete_and_private(monkeypatch, tmp_path):
    import pcc_agent.windows_sandbox as module

    monkeypatch.setattr(
        module,
        "os",
        SimpleNamespace(
            environ={
                "SystemRoot": str(tmp_path / "Windows"),
                "LOCALAPPDATA": "private-host-profile",
                "PCC_SECRET": "private-test-only",
                "HTTP_PROXY": "untrusted",
            }
        ),
    )
    root = tmp_path / "worker"
    root.mkdir()
    environment = worker_environment(root)
    for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "TEMP", "TMP"):
        path = Path(environment[key])
        assert path.is_dir() and path.is_relative_to(root)
    assert environment["SystemRoot"] == str(tmp_path / "Windows")
    assert environment["WINDIR"] == environment["SystemRoot"]
    assert "private-host-profile" not in environment.values()
    assert not {"PCC_SECRET", "HTTP_PROXY"}.intersection(environment)


@pytest.mark.parametrize("sid", ["S-1-5-21-123", "S-1-15-2-1", "S-1-15-2-2", "invalid"])
@pytest.mark.parametrize("remove", [False, True])
def test_runtime_acl_cannot_change_owner_or_shared_package_access(
    tmp_path, sid, remove
):
    with pytest.raises(ValueError, match="private AppContainer"):
        runtime_acl_args(tmp_path, sid, remove=remove)


def test_runtime_grant_and_cleanup_only_target_the_unique_container(tmp_path):
    sid = "S-1-15-2-111-222-333-444-555-666-777"
    grant = runtime_acl_args(tmp_path, sid)
    remove = runtime_acl_args(tmp_path, sid, remove=True)
    assert grant == [str(tmp_path), "/grant", f"*{sid}:RX", "/T", "/Q"]
    assert remove == [str(tmp_path), "/remove:g", f"*{sid}", "/T", "/Q"]
    assert not {"/reset", "/inheritance:r", "/grant:r"}.intersection(grant + remove)


@pytest.mark.parametrize("status", [0xC0000022, 1])
def test_worker_error_preserves_native_exit_status(status):
    worker = SimpleNamespace(wait=lambda: status)
    with pytest.raises(RuntimeError, match=f"0x{status:08X}") as exc:
        WindowsWorker.result(worker)
    if status == 0xC0000022:
        assert "EXE/DLL/ZIP" in str(exc.value)


def test_failed_probe_explains_loader_failure_without_advertising_compute(
    monkeypatch, caplog
):
    import pcc_agent.windows_sandbox as module

    closed = []

    def result():
        raise module.worker_exit_error(0xC0000022)

    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        module,
        "WindowsWorker",
        lambda *args: SimpleNamespace(result=result, close=lambda: closed.append(True)),
    )
    with caplog.at_level(logging.WARNING, logger="pcc.agent.windows"):
        assert not module.probe(SimpleNamespace(windows_worker_runtime="approved"))
    assert closed == [True]
    assert "0xC0000022" in caplog.text
