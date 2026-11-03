"""Windows startup diagnosis without relaxing isolation. FubukiHanasaki."""

from pathlib import Path
from types import SimpleNamespace
import logging
import threading
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


def stopping_worker(*, job=100):
    """A terminating process remains unsignaled until its pending I/O ends."""
    calls = []

    class Kernel:
        def TerminateJobObject(self, handle, code):
            calls.append("job terminate")
            return 1

        def TerminateProcess(self, handle, code):
            calls.append("process terminate")
            if job:
                raise OSError(5, "Duplicate termination during pending Job exit")
            return 1

        def WaitForSingleObject(self, handle, milliseconds):
            calls.append("wait")
            return 258 if calls.count("wait") == 1 else 0

        def GetExitCodeProcess(self, handle, code):
            code._obj.value = 1
            return 1

        def QueryInformationJobObject(self, handle, kind, data, size, returned):
            calls.append("job query")
            # The first query can still see the terminating process.
            data._obj.active_processes = int(calls.count("job query") == 1)
            return 1

    worker = WindowsWorker.__new__(WindowsWorker)
    worker.kernel = Kernel()
    worker.lock = threading.RLock()
    worker.job, worker.handle, worker.returncode = job, 101, None
    return worker, calls


def test_job_stop_waits_for_pending_exit_without_duplicate_termination():
    worker, calls = stopping_worker()
    worker.stop()
    worker.stop()  # Timer and foreground cleanup can both request stop.
    assert calls.count("job terminate") == 2
    assert calls.count("wait") >= 2 and calls.count("job query") >= 2
    assert "process terminate" not in calls
    assert worker.returncode == 1


def test_stop_without_job_retains_direct_process_fallback():
    worker, calls = stopping_worker(job=None)
    worker.stop()
    assert calls.count("process terminate") == 1
    assert "job terminate" not in calls and "job query" not in calls
    assert worker.returncode == 1


def test_job_termination_failure_is_not_ignored_or_retried_as_process_kill():
    worker, calls = stopping_worker()

    def denied(*args):
        raise OSError(5, "Job termination denied")

    worker.kernel.TerminateJobObject = denied
    with pytest.raises(OSError, match="Job termination denied"):
        worker.stop()
    assert calls == []
    assert worker.handle == 101 and worker.job == 100


def test_job_stop_requires_active_processes_zero_even_after_process_exit(monkeypatch):
    import pcc_agent.windows_sandbox as module

    worker, _ = stopping_worker()

    def active(handle, kind, data, size, returned):
        data._obj.active_processes = 1
        return 1

    worker.kernel.QueryInformationJobObject = active
    ticks = iter([0, 0, 0, 0, 11])
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda seconds: None),
    )
    with pytest.raises(RuntimeError, match="Cannot confirm Windows Job Object stopped"):
        worker.stop()
    assert worker.handle == 101 and worker.job == 100
