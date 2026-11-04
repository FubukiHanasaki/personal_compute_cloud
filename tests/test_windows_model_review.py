from managed_fixtures import configure_managed_memory

"""Logic/security regressions using only fake processes and mock HTTP. FubukiHanasaki."""

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import httpx
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.errors import WorkerStopUnconfirmed
from pcc_agent.services import OllamaBackend, ServiceManager
from pcc_agent.windows_managed import limits


class Backend:
    def __init__(self, *args):
        self.stopped = False

    def start(self):
        pass

    def unload(self):
        pass

    def stop(self):
        self.stopped = True


def manager(tmp_path, factory=Backend):
    cfg = AgentConfig(
        server="https://example.com",
        name="review",
        work_root=tmp_path,
        mode="shared",
        allocatable_cpu=2,
        allocatable_memory_mb=1024,
    )
    result = ServiceManager(cfg, factory=factory)
    configure_managed_memory(result, ["ollama:local"])
    result.sync(
        [
            {
                "definition_id": "model",
                "desired_state": "ready",
                "idle_seconds": 300,
                "config": {
                    "worker": "ollama:local",
                    "model": "local",
                    "resources": {"cpu": 1, "memory_mb": 512, "vram_mb": 0},
                },
            }
        ]
    )
    return result


@pytest.mark.parametrize("resource,reduced", [("cpu", 0.5), ("memory_mb", 256)])
def test_reused_model_respects_reduced_local_budget_and_drains(
    tmp_path, resource, reduced
):
    services = manager(tmp_path)
    service = services.acquire("model")
    services.ready(service)
    backend = service.backend
    setattr(
        services.config,
        "managed_commit_budget_mb"
        if resource == "memory_mb"
        else "allocatable_" + resource,
        reduced,
    )
    services.tick()
    assert not backend.stopped  # active requests drain rather than being aborted
    services.release(service)
    with pytest.raises(RuntimeError, match="allocatable"):
        services.acquire("model")
    services.tick()
    assert backend.stopped and service.backend is None


def test_shutdown_during_warmup_cannot_resurrect_service(tmp_path):
    entered, resume, finished = threading.Event(), threading.Event(), threading.Event()

    class Slow(Backend):
        def start(self):
            entered.set()
            assert resume.wait(3)

    services = manager(tmp_path, Slow)
    service = services.services["model"]
    original = services.release

    def release(*args, **kwargs):
        try:
            original(*args, **kwargs)
        finally:
            finished.set()

    services.release = release
    try:
        services.tick()
        assert entered.wait(1)
        backend = service.backend
        services.stop_all()
        resume.set()
        assert finished.wait(2)
        assert backend.stopped and service.backend is None
        assert service.state == "stopped" and service.active_requests == 0
        with pytest.raises(RuntimeError, match="stopped"):
            services.acquire("model")
        services.tick()
        assert service.backend is None
    finally:
        resume.set()
        services.stop_all()


def test_shutdown_continues_after_unconfirmed_backend_and_retains_identity(tmp_path):
    services = manager(tmp_path)
    unsafe = services.acquire("model")
    services.ready(unsafe)
    services.release(unsafe)
    backend = unsafe.backend

    def fail():
        raise OSError("Injected stop failure")

    backend.stop = fail
    from pcc_agent.services import Service

    other = Backend()
    services.services["other"] = Service("other", unsafe.config, 300, backend=other)
    with pytest.raises(WorkerStopUnconfirmed):
        services.stop_all()
    assert other.stopped and services.services["other"].backend is None
    assert unsafe.backend is backend and unsafe.state == "error"


@pytest.mark.parametrize("metric,value", [("cpu_load", 85), ("memory_load", 90)])
def test_manual_warmup_obeys_local_load_protection(tmp_path, metric, value):
    services = manager(tmp_path)
    services.capabilities = {metric: value}
    services.tick()
    assert services.services["model"].backend is None
    assert services.services["model"].active_requests == 0


def test_manual_warmup_cannot_exceed_discovered_physical_allowance(tmp_path):
    services = manager(tmp_path)
    services.capabilities["managed_commit_budget_mb"] = 256
    services.tick()
    assert services.services["model"].backend is None


@pytest.mark.parametrize("field", ["ollama_gpu_device", "comfyui_gpu_device"])
def test_gpu_selection_is_one_explicit_uuid_not_a_device_list(field):
    from pydantic import ValidationError

    for device in ("0", "0,1", "GPU-not-a-uuid", "GPU-a\n"):
        with pytest.raises(ValidationError):
            AgentConfig(server="https://example.com", name="review", **{field: device})
    assert getattr(
        AgentConfig(
            server="https://example.com",
            name="review",
            **{field: "GPU-12345678-1234-1234-1234-123456789abc"},
        ),
        field,
    ).endswith("123456789abc")


class Stream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    def __iter__(self):
        yield from self.chunks()

    def close(self):
        self.closed = True


def mock_backend(tmp_path, responder):
    cfg = AgentConfig(server="https://example.com", name="review", work_root=tmp_path)
    backend = OllamaBackend(cfg, "local", {"cpu": 1, "memory_mb": 512, "vram_mb": 0})
    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(responder)
    )
    backend.controller = SimpleNamespace(
        listener_ready=lambda port: True, stop=lambda: None
    )
    return backend


@pytest.mark.parametrize("case", ["metadata", "text", "compressed"])
def test_backend_response_limits_and_compression_refusal(tmp_path, case):
    stream = Stream(lambda: iter([b"x" * 8192] * 129))

    def respond(request):
        assert request.headers["Accept-Encoding"] == "identity"
        if case == "metadata":
            return httpx.Response(200, stream=stream)
        if case == "compressed":
            return httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=stream
            )
        return httpx.Response(200, json={"message": {"content": "雪" * 4}})

    backend = mock_backend(tmp_path, respond)
    try:
        with pytest.raises(RuntimeError, match="limit|Compressed"):
            if case == "text":
                backend.chat([{"role": "user", "content": "test"}], max_bytes=10)
            else:
                backend._json("GET", "/api/ps")
        assert case == "text" or stream.closed
    finally:
        backend.stop()


@pytest.mark.parametrize("case", ["deadline", "cancel"])
def test_stream_cannot_extend_deadline_or_ignore_stop(monkeypatch, tmp_path, case):
    import pcc_agent.services as module

    clock = [100.0]
    backend = None

    def chunks():
        yield b"a"
        if case == "deadline":
            clock[0] = 102.0
        else:
            backend.cancelled.set()
        yield b"b"

    stream = Stream(chunks)
    backend = mock_backend(tmp_path, lambda request: httpx.Response(200, stream=stream))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    try:
        with pytest.raises(RuntimeError, match="timed out|stopped"):
            backend._read_http("GET", "/api/chat", 100, deadline=101.0)
        assert stream.closed
    finally:
        backend.stop()


def test_job_memory_limit_cannot_wrap_size_t():
    with pytest.raises(ValueError):
        limits({"cpu": 1, "memory_mb": 2**64}, 8)


def test_chat_worker_passes_attempt_output_and_time_budget(tmp_path):
    from pcc_agent.services import InferenceWorker

    calls = []

    class Chat(Backend):
        def chat(self, messages, max_bytes, timeout_seconds):
            calls.append((messages, max_bytes, timeout_seconds))
            return {"result": {"content": "ok"}, "artifacts": []}

    services = manager(tmp_path, Chat)
    worker = InferenceWorker(
        services,
        {
            "template": {"id": "model", "worker": "ollama:local"},
            "input": {"messages": [{"role": "user", "content": "hi"}]},
            "limits": {"output_bytes": 23, "timeout_seconds": 17},
        },
    )
    try:
        assert worker.future.result(timeout=2)["result"]["content"] == "ok"
        assert calls == [([{"role": "user", "content": "hi"}], 23, 17)]
    finally:
        worker.close()
        services.stop_all()


def test_unconfirmed_model_stop_keeps_attempt_journal_without_ack(tmp_path):
    from pcc_agent.execution import execute

    class Unsafe(Backend):
        def chat(self, *args):
            raise ValueError("Injected generation failure")

        def stop(self):
            raise OSError("Injected unconfirmed stop")

        def recovery_record(self):
            return {"managed_job": "Local\\PCC.Managed." + "a" * 32}

    services = manager(tmp_path, Unsafe)
    paths = []

    def post(path, json):
        paths.append(path)
        assert not path.endswith(("/complete", "/fail"))
        return httpx.Response(
            200,
            json={"cancel_requested": False, "lease_expires_at": time.time() + 60},
            request=httpx.Request("POST", "https://example.com" + path),
        )

    agent = SimpleNamespace(
        config=services.config,
        services=services,
        heartbeat=lambda: None,
        client=SimpleNamespace(post=post),
    )
    with pytest.raises(OSError, match="unconfirmed stop"):
        execute(
            agent,
            {
                "job_id": "job",
                "attempt_id": "attempt",
                "lease_token": "test-only",
                "deadline": time.time() + 60,
                "template": {"id": "model", "worker": "ollama:local"},
                "input": {"messages": [{"role": "user", "content": "hi"}]},
                "limits": {
                    "cpu": 1,
                    "memory_mb": 512,
                    "vram_mb": 0,
                    "output_bytes": 1024,
                    "timeout_seconds": 60,
                },
            },
        )
    journal = json.loads((tmp_path / "active-attempt.json").read_text())
    assert journal["attempt_id"] == "attempt"
    assert journal["managed_job"] == "Local\\PCC.Managed." + "a" * 32
    assert services.services["model"].backend is not None


def test_partial_comfy_outputs_are_not_returned_or_cleaned(tmp_path):
    from pcc_agent.comfyui import ComfyUIBackend
    from test_comfyui import config

    backend = ComfyUIBackend(
        config(tmp_path), "local-image", {"cpu": 1, "memory_mb": 512, "vram_mb": 0}
    )
    backend.controller = SimpleNamespace(
        listener_ready=lambda port: True, stop=lambda: None
    )
    backend.client.close()
    paths, complete = [], [False]
    output = Path(backend.staging.name) / "output"
    output.mkdir()
    (output / "owned.png").write_bytes(b"test")

    def respond(request):
        paths.append(request.url.path)
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "review"})
        if request.url.path == "/history/review":
            done = complete[0]
            complete[0] = True
            assert (output / "owned.png").exists()
            return httpx.Response(
                200,
                json={
                    "review": {
                        "status": {"completed": done},
                        "outputs": {
                            "save": {
                                "images": [
                                    {
                                        "filename": "owned.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            assert paths.count("/history/review") == 2
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfixture")
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"devices": []})
        if request.url.path == "/history":
            assert json.loads(request.content) == {"delete": ["review"]}
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    backend.client = httpx.Client(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)
    )
    try:
        backend.generate({"prompt": "test", "size": "256x256"}, 1024)
        assert not list(output.iterdir())
    finally:
        backend.stop()
