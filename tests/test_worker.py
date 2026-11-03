import json
from pathlib import Path
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.platform import isolation_capabilities
from pcc_agent.sandbox import SandboxedWorker


def test_real_isolated_compute(tmp_path):
    if not isolation_capabilities():
        pytest.skip("Host does not permit nonprivileged bubblewrap")
    config = AgentConfig(server="https://example.com", name="test", work_root=tmp_path)
    payload = {
        "template": {"worker": "compute.stats"},
        "input": {"values": [1, 2, 3]},
        "limits": {
            "cpu": 1,
            "memory_mb": 256,
            "timeout_seconds": 10,
            "output_bytes": 1024 * 1024,
        },
    }
    worker = SandboxedWorker(config, payload)
    try:
        worker.process.wait(timeout=15)
        assert worker.result()["result"]["sum"] == 6
    finally:
        worker.close()


def test_submission_to_real_worker_to_download(env, tmp_path):
    if not isolation_capabilities():
        pytest.skip("Host does not permit nonprivileged bubblewrap")
    from types import SimpleNamespace
    from conftest import register_node
    from test_jobs import template, submit, claim
    from pcc_agent.execution import execute

    app, client, auth, _ = env
    template(client, auth)
    _, node_auth, _ = register_node(client, auth)
    jid = submit(client, auth).json()["job_id"]
    payload = claim(client, node_auth).json()

    class Transport:
        def post(self, path, json):
            return client.post(path, json=json, headers=node_auth)

    config = AgentConfig(
        enable_compute=True,
        enable_synthetic_ai=True,
        server="https://example.com",
        name="test",
        work_root=tmp_path,
        mode="shared",
    )
    agent = SimpleNamespace(config=config, client=Transport(), heartbeat=lambda: None)
    execute(agent, payload)
    status = client.get(f"/api/v1/jobs/{jid}", headers=auth).json()
    assert status["status"] == "completed", status
    assert status["result"]["sum"] == 6
    artifact = client.get(f"/api/v1/jobs/{jid}/artifacts", headers=auth).json()[
        "items"
    ][0]
    assert client.get(artifact["download_url"], headers=auth).json()["mean"] == 2


def test_lost_completion_ack_is_retried_without_overwrite(env, tmp_path):
    if not isolation_capabilities():
        pytest.skip("Requires Linux bubblewrap")
    from types import SimpleNamespace
    import httpx
    from conftest import register_node
    from test_jobs import template, submit, claim
    from pcc_agent.execution import execute

    app, client, auth, _ = env
    template(client, auth)
    _, nh, _ = register_node(client, auth)
    jid = submit(client, auth).json()["job_id"]

    class LostAck:
        lost = False

        def post(self, path, json):
            response = client.post(path, json=json, headers=nh)
            if path.endswith("/complete") and not self.lost:
                self.lost = True
                raise httpx.ReadTimeout("Injected lost acknowledgement")
            return response

    config = AgentConfig(
        enable_compute=True,
        enable_synthetic_ai=True,
        server="https://example.com",
        name="test",
        mode="shared",
        work_root=tmp_path,
    )
    execute(
        SimpleNamespace(config=config, client=LostAck(), heartbeat=lambda: None),
        claim(client, nh).json(),
    )
    assert (
        client.get(f"/api/v1/jobs/{jid}", headers=auth).json()["status"] == "completed"
    )
    assert not (tmp_path / "active-attempt.json").exists()


def test_lease_guard_stops_worker_while_network_thread_is_blocked(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    import httpx
    import threading
    import time
    import pcc_agent.execution as execution

    stopped = threading.Event()

    class Worker:
        def __init__(self, *args):
            pass

        def poll(self):
            return 0 if stopped.is_set() else None

        def stop(self):
            stopped.set()

        def close(self):
            self.stop()

        def result(self):
            raise AssertionError("Expired execution must not produce a result")

    class BlockedNetwork:
        def post(self, path, json):
            if path.endswith("/events"):
                assert stopped.wait(timeout=2), (
                    "Guard must run independently of network thread"
                )
            data = {"cancel_requested": False, "lease_expires_at": time.time() + 1.2}
            return httpx.Response(
                200,
                json=data,
                request=httpx.Request("POST", "https://example.com" + path),
            )

    monkeypatch.setattr(execution, "SandboxedWorker", Worker)
    config = AgentConfig(
        enable_compute=True,
        enable_synthetic_ai=True,
        server="https://example.com",
        name="test",
        mode="shared",
        work_root=tmp_path,
    )
    execution.execute(
        SimpleNamespace(config=config, client=BlockedNetwork(), heartbeat=lambda: None),
        {
            "job_id": "test",
            "attempt_id": "a",
            "lease_token": "t",
            "template": {"worker": "compute.stats"},
            "deadline": time.time() + 10,
        },
    )
    assert stopped.is_set()
    assert not (tmp_path / "active-attempt.json").exists()


@pytest.mark.parametrize(
    "mode,owner",
    [("private", "different-user"), ("private", None), ("reserved", "different-user")],
)
def test_local_policy_change_before_launch_refuses_claim(
    monkeypatch, tmp_path, mode, owner
):
    from types import SimpleNamespace
    import httpx, time
    import pcc_agent.execution as execution

    def forbidden_launch(*args):
        raise AssertionError(
            "Must not launch a worker after local policy denies the claim"
        )

    monkeypatch.setattr(execution, "SandboxedWorker", forbidden_launch)
    requests = []

    class Transport:
        def post(self, path, json):
            requests.append((path, json))
            return httpx.Response(
                200,
                json={"status": "failed"},
                request=httpx.Request("POST", "https://example.com" + path),
            )

    config = AgentConfig(
        server="https://example.com",
        name="node",
        work_root=tmp_path,
        mode=mode,
        enable_compute=True,
    )
    agent = SimpleNamespace(
        config=config,
        identity={"owner_id": "node-owner"},
        heartbeat=lambda: None,
        client=Transport(),
    )
    execution.execute(
        agent,
        {
            "job_id": "test",
            "owner_id": owner,
            "attempt_id": "attempt",
            "lease_token": "lease",
            "deadline": time.time() + 10,
            "template": {"worker": "compute.stats"},
        },
    )
    assert len(requests) == 1 and requests[0][0].endswith("/fail")
    assert requests[0][1]["stopped"] is True


def test_real_sandbox_cannot_read_host_credentials_or_connect_to_host_loopback(
    monkeypatch, tmp_path
):
    if not isolation_capabilities():
        pytest.skip("Requires Linux nonprivileged bubblewrap")
    import shutil, socket
    import pcc_agent.sandbox as sandbox

    original = Path(sandbox.__file__)
    probe = tmp_path / "probe"
    probe.mkdir()
    secret = tmp_path / "host-credential"
    secret.write_text("test-only-secret")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    shutil.copyfile(original.with_name("limit_exec.py"), probe / "limit_exec.py")
    (probe / "worker.py").write_text(f"""import json, socket, sys
sys.stdin.read()
secret_access = False
host_network = False
try:
    open({str(secret)!r}).read()
    secret_access = True
except OSError:
    pass
try:
    socket.create_connection(("127.0.0.1", {port}), timeout=1).close()
    host_network = True
except OSError:
    pass
print(json.dumps({{"result": {{"secret_access": secret_access, "host_network": host_network}}, "artifacts": []}}))
""")
    monkeypatch.setattr(sandbox, "__file__", str(probe / "sandbox.py"))
    config = AgentConfig(server="https://example.com", name="test", work_root=tmp_path)
    worker = sandbox.SandboxedWorker(
        config,
        {
            "template": {"worker": "compute.stats"},
            "input": {"values": [1]},
            "limits": {
                "cpu": 1,
                "memory_mb": 256,
                "timeout_seconds": 10,
                "output_bytes": 1024 * 1024,
            },
        },
    )
    try:
        worker.process.wait(timeout=15)
        assert worker.result()["result"] == {
            "secret_access": False,
            "host_network": False,
        }
    finally:
        worker.close()
        listener.close()
