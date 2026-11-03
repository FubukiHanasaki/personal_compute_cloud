"""Model process platform contracts, without Windows or model execution. Fubuki."""

import ctypes as C
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import httpx
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.windows_managed import WindowsManagedProcess, limits


def test_cpu_only_comfyui_does_not_import_cuda_runtime(monkeypatch):
    from pcc_agent.managed_exec import gpu_budget

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
    monkeypatch.setitem(sys.modules, "torch", None)
    gpu_budget(0)


@pytest.mark.parametrize("kind", ["ollama", "comfyui"])
def test_explicit_gpu_refuses_silent_cpu_fallback(tmp_path, kind):
    from pcc_agent.services import OllamaBackend
    from pcc_agent.comfyui import ComfyUIBackend
    from test_comfyui import config

    cfg = config(tmp_path)
    cfg.ollama_gpu_device = cfg.comfyui_gpu_device = (
        "GPU-12345678-1234-1234-1234-123456789abc"
    )
    backend = (OllamaBackend if kind == "ollama" else ComfyUIBackend)(
        cfg, "local-image", {"cpu": 1, "memory_mb": 1024, "vram_mb": 1024}
    )
    backend.controller = Controller()
    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://localhost",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"models": [{"size_vram": 0}], "devices": [{"type": "cpu"}]}
            )
        ),
    )
    try:
        with pytest.raises(RuntimeError, match="GPU|CUDA"):
            backend.check_vram()
        assert backend.cancelled.is_set()
    finally:
        backend.stop()


def test_shipped_windows_model_config_and_workflow_are_consistent(tmp_path):
    import json
    import tomllib
    from pcc_agent.comfyui import approved_workflow
    from pcc_agent.config import ComfyWorkflow

    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads(
        (root / "deploy/agent-windows-models.example.toml").read_text()
    )
    binding = config["comfyui_workflows"]["local-image"]
    workflow = root / "deploy/workflows/sd15-api.example.json"
    cfg = AgentConfig(
        server="https://example.com",
        name="sample",
        work_root=tmp_path,
        comfyui_workflows={
            "local-image": ComfyWorkflow(
                file=workflow,
                prompt_node=binding["prompt_node"],
                latent_node=binding["latent_node"],
            )
        },
    )
    graph = approved_workflow(
        cfg, "local-image", {"prompt": "a lake", "size": "512x512"}
    )
    assert graph["6"]["inputs"]["text"] == "a lake"
    assert graph["5"]["inputs"]["width"] == 512
    assert json.loads(workflow.read_text())["6"]["inputs"]["text"] == "test"


def test_windows_limits_cover_aggregate_memory_cpu_and_children():
    info, rate = limits({"cpu": 2, "memory_mb": 4096}, 8)
    assert info.basic.flags & 0x2000  # kill on close
    assert not info.basic.flags & (0x800 | 0x1000)  # no breakaway
    assert info.basic.processes == 256
    assert info.job_memory == info.process_memory == 4096 * 2**20
    assert tuple(rate) == (5, 2500)
    with pytest.raises(ValueError):
        limits({"cpu": float("nan"), "memory_mb": 512}, 8)


@pytest.fixture
def kernel(monkeypatch):
    import pcc_agent.windows_managed as module

    class Kernel:
        def __init__(self):
            self.attributes, self.closed = [], []
            self.terminated = False
            self.flags = None

        def CreateJobObjectW(self, *args):
            return 100

        def SetInformationJobObject(self, *args):
            return 1

        def InitializeProcThreadAttributeList(self, buffer, count, flags, size):
            size._obj.value = 128
            return bool(buffer)

        def UpdateProcThreadAttribute(self, buffer, flags, attribute, *args):
            self.attributes.append(attribute)
            return 1

        def CreateProcessW(
            self,
            executable,
            command,
            pa,
            ta,
            inherit,
            flags,
            env,
            cwd,
            startup,
            process,
        ):
            assert set(self.attributes) == {0x2000D, 0x20002}
            assert inherit and flags & 0x4 and flags & 0x80000
            self.flags = flags
            process._obj.process, process._obj.thread, process._obj.pid = 101, 102, 103
            return 1

        def ResumeThread(self, thread):
            return 1

        def WaitForSingleObject(self, *args):
            return 258 if args[0] == 105 else 0

        def OpenProcess(self, access, inherit, pid):
            assert access == 0x101000 and not inherit
            return 105

        def IsProcessInJob(self, handle, job, member):
            assert handle == 105 and job == 100
            member._obj.value = 1
            return 1

        def GetExitCodeProcess(self, handle, code):
            code._obj.value = 0
            return 1

        def QueryInformationJobObject(self, handle, kind, data, size, returned):
            if kind == 1:
                data._obj.active_processes = 0
            elif kind == 3:
                module.DWORD.from_buffer(data, 4).value = 1
                (module.SIZE * 1).from_buffer(data, 8)[0] = 104
            return 1

        def TerminateJobObject(self, *args):
            self.terminated = True
            return 1

        def CloseHandle(self, handle):
            self.closed.append(handle)
            return 1

        def DeleteProcThreadAttributeList(self, *args):
            pass

    fake = Kernel()
    monkeypatch.setattr(module, "api", lambda: (fake, None, None))
    monkeypatch.setattr(C, "set_last_error", lambda _: None, raising=False)
    monkeypatch.setattr(C, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setitem(
        sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd)
    )
    return fake


def test_windows_spawn_attaches_before_resume_and_stops_after_parent_exit(
    kernel, tmp_path
):
    process = WindowsManagedProcess({"cpu": 1, "memory_mb": 512})
    with (tmp_path / "out").open("ab") as output:
        process.start(["approved.exe", "serve"], {"SystemRoot": "test"}, output)
    assert process.poll() == 0
    assert process.process_ids() == [104]  # a surviving child, despite parent exit
    process.close()
    assert kernel.terminated and {100, 101, 102} <= set(kernel.closed)
    with pytest.raises(RuntimeError, match="once"):
        process.start([], {}, None)


def test_name_collision_never_terminates_an_existing_job(kernel, monkeypatch, tmp_path):
    monkeypatch.setattr(C, "get_last_error", lambda: 183, raising=False)
    process = WindowsManagedProcess({"cpu": 1, "memory_mb": 512})
    with (
        (tmp_path / "out").open("ab") as output,
        pytest.raises(RuntimeError, match="exists"),
    ):
        process.start(["approved.exe"], {}, output)
    assert not kernel.terminated
    assert 100 in kernel.closed


def test_unconfirmed_tree_stop_keeps_job_handles(kernel, monkeypatch):
    import pcc_agent.windows_managed as module

    process = WindowsManagedProcess({"cpu": 1, "memory_mb": 512})
    process.job, process.handle = 100, 101

    def active(handle, kind, data, size, returned):
        data._obj.active_processes = 1
        return 1

    ticks = iter([0, 11])
    monkeypatch.setattr(kernel, "QueryInformationJobObject", active)
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda seconds: None),
    )
    with pytest.raises(RuntimeError, match="tree stopped"):
        process.close()
    assert process.job == 100 and process.handle == 101
    assert kernel.closed == []


def test_windows_controller_dispatch_and_recovery_identity(monkeypatch):
    import pcc_agent.managed_process as module
    import pcc_agent.windows_managed as windows

    class Job:
        def __init__(self, resources, name):
            self.resources, self.name, self.closed = resources, name, False

        def start(self, command, env, stdout, cwd):
            raise OSError("Injected spawn failure")

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(windows, "WindowsManagedProcess", Job)
    controller = module.ManagedProcess(
        {"cpu": 1, "memory_mb": 512}, "pcc-worker-" + "a" * 32
    )
    with pytest.raises(OSError):
        controller.start(["approved.exe"], {}, None)
    assert controller.process is not None  # partial construction remains owned
    controller.stop()
    assert controller.process.closed
    assert controller.recovery_record() == {
        "managed_job": "Local\\PCC.Managed." + "a" * 32
    }


def test_windows_port_preflight_is_exclusive(monkeypatch):
    import pcc_agent.services as module

    calls = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            calls.append(args)

        def bind(self, address):
            calls.append(address)

    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        module,
        "socket",
        SimpleNamespace(
            socket=Socket, SOL_SOCKET=1, SO_REUSEADDR=2, SO_EXCLUSIVEADDRUSE=-5
        ),
    )
    module.check_loopback_port(11435)
    assert calls == [(1, -5, 1), ("127.0.0.1", 11435)]


@pytest.mark.parametrize("case", ["active_job", "live_agent", "wrong_platform"])
def test_reconcile_never_acknowledges_a_live_or_foreign_controller(
    monkeypatch, tmp_path, case
):
    import json
    import pcc_agent.cli as cli
    import pcc_agent.runtime as runtime
    import pcc_agent.windows_managed as windows

    record = {
        "job_id": "job",
        "attempt_id": "attempt",
        "lease_token": "lease",
        "managed_job": "Local\\PCC.Managed." + "a" * 32,
    }
    if case == "live_agent":
        record.update(controller_pid=12, controller_created_at=34)
        monkeypatch.setattr(cli, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(windows.psutil, "pid_exists", lambda pid: True)
        monkeypatch.setattr(
            windows.psutil,
            "Process",
            lambda pid: SimpleNamespace(create_time=lambda: 34),
        )
    elif case == "wrong_platform":
        record.pop("managed_job")
        record["managed_scope"] = "pcc-worker-" + "a" * 32
    (tmp_path / "active-attempt.json").write_text(json.dumps(record))
    config = SimpleNamespace(work_root=tmp_path)
    monkeypatch.setattr(cli.AgentConfig, "load", lambda path: config)
    monkeypatch.setattr(cli, "os", SimpleNamespace(name="nt"))

    def forbidden(*args, **kwargs):
        raise AssertionError("Must not acknowledge an unsafe reconciliation")

    monkeypatch.setattr(
        runtime,
        "Agent",
        lambda *args, **kwargs: SimpleNamespace(client=SimpleNamespace(post=forbidden)),
    )
    monkeypatch.setattr(windows, "active_job", lambda name: True)
    monkeypatch.setattr(sys, "argv", ["pcc-agent", "reconcile", "--confirm-stopped"])
    with pytest.raises(SystemExit, match="still alive|still active|original Linux"):
        cli.main()
    assert (tmp_path / "active-attempt.json").exists()


def test_listener_must_belong_to_a_job_process(kernel, monkeypatch):
    import pcc_agent.windows_managed as module

    process = WindowsManagedProcess({"cpu": 1, "memory_mb": 512})
    process.job = 100
    requested = []

    def owner(pid):
        requested.append(pid)
        return SimpleNamespace(
            net_connections=lambda kind: [
                SimpleNamespace(
                    status="LISTEN",
                    laddr=SimpleNamespace(ip="127.0.0.1", port=11435),
                )
            ]
        )

    monkeypatch.setattr(module.psutil, "Process", owner)
    assert process.owns_listener(11435)
    assert not process.owns_listener(11434)
    assert requested == [104, 104]
    assert kernel.closed == [105, 105]


@pytest.mark.parametrize("case", ["reused_pid", "exited_before", "exited_during"])
def test_stale_job_pid_cannot_authorize_a_foreign_listener(kernel, monkeypatch, case):
    import pcc_agent.windows_managed as module

    process = WindowsManagedProcess({"cpu": 1, "memory_mb": 512})
    process.job = 100
    calls = []

    def member(handle, job, result):
        result._obj.value = case != "reused_pid"
        return 1

    states = iter([0] if case == "exited_before" else [258, 0])
    monkeypatch.setattr(kernel, "IsProcessInJob", member)
    monkeypatch.setattr(kernel, "WaitForSingleObject", lambda *args: next(states))

    def owner(pid):
        calls.append(pid)
        return SimpleNamespace(
            net_connections=lambda kind: [
                SimpleNamespace(
                    status="LISTEN", laddr=SimpleNamespace(ip="127.0.0.1", port=11435)
                )
            ]
        )

    monkeypatch.setattr(module.psutil, "Process", owner)
    assert not process.owns_listener(11435)
    assert calls == ([104] if case == "exited_during" else [])
    assert kernel.closed == [105]


def test_managed_windows_environment_does_not_inherit_credentials(
    monkeypatch, tmp_path
):
    import pcc_agent.managed_process as module

    monkeypatch.setattr(
        module,
        "os",
        SimpleNamespace(
            name="nt",
            environ={
                "SystemRoot": str(tmp_path / "Windows"),
                "PCC_SECRET": "private-test-only",
                "HTTP_PROXY": "http://untrusted",
                "PATH": "untrusted",
            },
            pathsep=";",
        ),
    )
    config = AgentConfig(server="https://example.com", name="win", work_root=tmp_path)
    env = module.environment(config, tmp_path / "runtime/python.exe")
    assert "PCC_SECRET" not in env and "HTTP_PROXY" not in env
    assert "untrusted" not in env["PATH"]
    assert Path(env["TEMP"]).is_relative_to(tmp_path)
    assert env["USERPROFILE"] != os.environ.get("USERPROFILE")


def test_linux_controller_preserves_cgroup_limits_and_own_scope_cleanup(monkeypatch):
    import pcc_agent.managed_process as module

    calls = []
    process = SimpleNamespace(pid=123, poll=lambda: 0, wait=lambda timeout: 0)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append(command) or process,
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append(command) or SimpleNamespace(returncode=0)
        ),
    )
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix"))
    controller = module.ManagedProcess(
        {"cpu": 2, "memory_mb": 4096}, "pcc-worker-" + "a" * 32
    )
    controller.start(["/approved/ollama", "serve"], {}, None)
    controller.stop()
    assert "MemoryMax=4096M" in calls[0] and "CPUQuota=200%" in calls[0]
    assert calls[-1][-1] == "pcc-worker-" + "a" * 32 + ".scope"
    assert controller.recovery_record() == {"managed_scope": "pcc-worker-" + "a" * 32}


def test_windows_discovery_requires_model_job_probe_without_claiming_compute(
    monkeypatch, tmp_path
):
    import pcc_agent.platform as module

    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module, "gpu_metrics", lambda: [])
    config = AgentConfig(
        server="https://example.com",
        name="win",
        work_root=tmp_path,
        ollama_executable="approved.exe",
        ollama_models=["local-model"],
        enable_compute=True,
    )
    assert module.discover(config, isolation=[], managed=False)["workers"] == []
    caps = module.discover(config, isolation=[], managed=True)
    assert caps["workers"] == ["ollama:local-model"]
    assert caps["isolation"] == ["windows-managed-job"]


def test_windows_probe_failure_never_advertises_models(monkeypatch):
    import pcc_agent.platform as module
    import pcc_agent.windows_managed as windows
    from pcc_agent.errors import WorkerStopUnconfirmed

    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows, "probe", lambda: False)
    assert not module.managed_isolation_available()

    def uncertain():
        raise WorkerStopUnconfirmed("Injected unconfirmed probe")

    monkeypatch.setattr(windows, "probe", uncertain)
    with pytest.raises(WorkerStopUnconfirmed):
        module.managed_isolation_available()


class Controller:
    def __init__(self):
        self.command = self.env = None
        self.ready = True
        self.stopped = False

    def start(self, command, env, stdout, cwd=None):
        self.command, self.env = command, env
        return SimpleNamespace(poll=lambda: None)

    def listener_ready(self, port):
        return self.ready

    def stop(self):
        self.stopped = True


def test_ollama_lifecycle_uses_controller_and_refuses_unowned_endpoint(
    monkeypatch, tmp_path
):
    import pcc_agent.services as module
    import pcc_agent.platform as platform

    executable = tmp_path / "ollama.exe"
    executable.touch()
    config = AgentConfig(
        server="https://example.com",
        name="win",
        work_root=tmp_path,
        ollama_executable=str(executable),
        ollama_models=["local"],
        ollama_model_root=tmp_path / "models",
    )
    monkeypatch.setattr(platform, "managed_isolation_available", lambda: True)
    monkeypatch.setattr(module, "check_loopback_port", lambda port: None)
    backend = module.OllamaBackend(
        config, "local", {"cpu": 1, "memory_mb": 512, "vram_mb": 0}
    )
    controller = backend.controller = Controller()
    calls = []

    def request(req):
        calls.append(req.url.path)
        return httpx.Response(
            200, json={"models": [], "message": {"content": "mock reply"}}
        )

    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(request)
    )
    try:
        backend.start()
        assert controller.command == [str(executable), "serve"]
        assert controller.env["CUDA_VISIBLE_DEVICES"] == "-1"
        assert controller.env["OLLAMA_VULKAN"] == "0"
        assert backend.chat([])["result"]["content"] == "mock reply"
        backend.unload()
        controller.ready = False
        before = len(calls)
        with pytest.raises(RuntimeError, match="not owned"):
            backend.chat([])
        assert len(calls) == before
    finally:
        backend.stop()
    assert controller.stopped


def test_comfyui_launch_uses_controller_fixed_flags_and_cpu_budget(
    monkeypatch, tmp_path
):
    import pcc_agent.comfyui as module
    from test_comfyui import config

    cfg = config(tmp_path)
    executable, script = tmp_path / "python.exe", tmp_path / "main.py"
    executable.touch()
    script.touch()
    cfg.comfyui_python, cfg.comfyui_script = str(executable), str(script)
    monkeypatch.setattr(module, "managed_isolation_available", lambda: True)
    monkeypatch.setattr(module, "check_loopback_port", lambda port: None)
    backend = module.ComfyUIBackend(
        cfg, "local-image", {"cpu": 1, "memory_mb": 1024, "vram_mb": 0}
    )
    controller = backend.controller = Controller()
    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://localhost",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"devices": []})
        ),
    )
    try:
        backend.start()
        assert controller.command[:3] == [str(executable), "-I", "-B"]
        assert all(
            flag in controller.command
            for flag in (
                "--cpu",
                "--disable-all-custom-nodes",
                "--disable-api-nodes",
                "--disable-dynamic-vram",
            )
        )
        assert "--scope" not in controller.command
        assert controller.env["CUDA_VISIBLE_DEVICES"] == "-1"
        assert controller.env["HF_HUB_OFFLINE"] == "1"
    finally:
        backend.stop()
    assert controller.stopped
