import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.services import ServiceManager
from conftest import register_node


class FakeBackend:
    def __init__(self, *args):
        self.started = self.unloaded = self.stopped = False

    def start(self):
        self.started = True

    def unload(self):
        self.unloaded = True

    def stop(self):
        self.stopped = True


def test_lifecycle_idle_active_guard_and_gpu_lock(tmp_path):
    clock = [1000]
    config = AgentConfig(
        server="https://test.example",
        name="test",
        work_root=tmp_path,
        allocatable_cpu=2,
        allocatable_memory_mb=1024,
        allocatable_vram_mb=1024,
    )
    manager = ServiceManager(config, factory=FakeBackend, clock=lambda: clock[0])

    def command(id):
        return {
            "definition_id": id,
            "desired_state": "auto",
            "idle_seconds": 5,
            "config": {
                "worker": "ollama:model",
                "model": "model",
                "resources": {"cpu": 1, "memory_mb": 512, "vram_mb": 512},
            },
        }

    manager.sync([command("a"), command("b")])
    active = manager.acquire("a")
    manager.ready(active)
    backend = active.backend
    clock[0] += 10
    manager.tick()
    assert not backend.stopped
    with pytest.raises(RuntimeError, match="lock"):
        manager.acquire("b")
    manager.release(active)
    clock[0] += 6
    manager.tick()
    assert backend.unloaded and backend.stopped
    assert active.state == "stopped"


def test_models_and_service_control_remain_separate_from_jobs(env):
    app, c, auth, _ = env
    assert (
        c.post(
            "/api/v1/admin/templates",
            headers=auth,
            json={
                "id": "chat",
                "name": "测试",
                "kind": "ai.chat",
                "worker": "synthetic.chat",
                "model": "explicit-test",
                "public": True,
            },
        ).status_code
        == 201
    )
    identity, node, caps = register_node(c, auth, workers=["synthetic.chat"])
    assert c.get("/api/v1/models", headers=auth).json()["data"][0]["synthetic"]
    services = c.get("/api/v1/admin/services", headers=auth).json()["items"]
    assert services[0]["state"] == "stopped"
    assert (
        c.put(
            "/api/v1/admin/services/" + services[0]["id"],
            headers=auth,
            json={"desired_state": "ready"},
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/v1/chat/completions",
            headers=auth,
            json={
                "model": "explicit-test",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.7,
            },
        ).status_code
        == 422
    )


def test_manual_model_load_does_not_block_heartbeat_reporting(tmp_path):
    import threading, time

    release = threading.Event()
    started = threading.Event()

    class SlowBackend(FakeBackend):
        def start(self):
            started.set()
            assert release.wait(3)
            super().start()

    config = AgentConfig(
        server="https://example.com",
        name="test",
        work_root=tmp_path,
        mode="shared",
        allocatable_memory_mb=1024,
    )
    manager = ServiceManager(config, factory=SlowBackend)
    manager.sync(
        [
            {
                "definition_id": "slow",
                "desired_state": "ready",
                "idle_seconds": 300,
                "config": {
                    "worker": "ollama:model",
                    "model": "model",
                    "resources": {"cpu": 1, "memory_mb": 512, "vram_mb": 0},
                },
            }
        ]
    )
    try:
        manager.tick()
        assert started.wait(1)
        assert manager.reports()[0]["state"] == "starting"
        assert manager.reports()[0]["active_requests"] == 1
    finally:
        release.set()
        until = time.monotonic() + 3
        while manager.reports()[0]["active_requests"] and time.monotonic() < until:
            time.sleep(0.01)
        manager.stop_all()


def test_failed_stop_retains_backend_and_quarantines_resources(tmp_path):
    class FailedStop(FakeBackend):
        def stop(self):
            raise RuntimeError("Injected failure to confirm managed child termination")

    config = AgentConfig(server="https://example.com", name="test", work_root=tmp_path)
    manager = ServiceManager(config, factory=FailedStop)
    manager.sync(
        [
            {
                "definition_id": "unsafe",
                "desired_state": "auto",
                "idle_seconds": 1,
                "config": {
                    "worker": "ollama:model",
                    "model": "model",
                    "resources": {"cpu": 1, "memory_mb": 256, "vram_mb": 0},
                },
            }
        ]
    )
    active = manager.acquire("unsafe")
    manager.ready(active)
    manager.release(active)
    backend = active.backend
    assert manager._stop(active) is False
    assert active.state == "error" and active.backend is backend
    with pytest.raises(RuntimeError, match="confirmation"):
        manager.acquire("unsafe")
    manager.sync(
        [
            {
                "definition_id": "next",
                "desired_state": "auto",
                "idle_seconds": 1,
                "config": active.config,
            }
        ]
    )
    with pytest.raises(RuntimeError, match="confirmation"):
        manager.acquire("next")
    assert manager.services["next"].backend is None


def test_loopback_probe_reuses_time_wait_but_refuses_a_live_server():
    import socket
    from pcc_agent.services import check_loopback_port

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen()
    try:
        import pytest

        with pytest.raises(OSError):
            check_loopback_port(port)
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = listener.accept()
            connection.shutdown(socket.SHUT_RDWR)
            connection.close()
            assert client.recv(1) == b""
    finally:
        listener.close()
    check_loopback_port(port)
