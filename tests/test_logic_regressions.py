"""Regression cases prepared during static review; Author: Fubuki."""

import time
from types import SimpleNamespace
import httpx
import pytest
from fastapi import HTTPException
from conftest import register_node
from test_jobs import template, submit, claim, lease
from pcc_server.models import JobAttempt


def test_cancellation_survives_expiry_and_refuses_retry(env):
    app, client, auth, _ = env
    template(client, auth)
    _, node, _ = register_node(client, auth)
    job = submit(client, auth).json()["job_id"]
    attempt = claim(client, node).json()
    client.post(f"/api/v1/jobs/{job}/cancel", headers=auth)
    with app.state.sessions() as db:
        db.get(JobAttempt, attempt["attempt_id"]).lease_expires_at = time.time() - 1
        db.commit()
    assert (
        client.get(f"/api/v1/jobs/{job}", headers=auth).json()["status"]
        == "cancel_requested"
    )
    stopped = client.post(
        f"/internal/v1/jobs/{job}/fail",
        headers=node,
        json={
            **lease(attempt),
            "stopped": True,
            "retryable": True,
            "message": "stopped",
        },
    )
    assert stopped.json()["status"] == "cancelled"
    assert claim(client, node, "new" * 10).status_code == 204


def test_lost_claim_can_recover_expired_attempt_without_reexecution(env):
    app, client, auth, _ = env
    template(client, auth)
    _, node, _ = register_node(client, auth)
    job = submit(client, auth).json()["job_id"]
    attempt = claim(client, node).json()
    with app.state.sessions() as db:
        db.get(JobAttempt, attempt["attempt_id"]).lease_expires_at = time.time() - 1
        db.commit()
    replay = claim(client, node).json()
    assert replay["attempt_id"] == attempt["attempt_id"]
    assert (
        client.post(
            f"/internal/v1/jobs/{job}/renew", headers=node, json=lease(replay)
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/internal/v1/jobs/{job}/complete",
            headers=node,
            json={
                **lease(replay),
                "result": {},
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/internal/v1/jobs/{job}/fail",
            headers=node,
            json={
                **lease(replay),
                "stopped": True,
                "message": "Never started after lost response",
            },
        ).json()["status"]
        == "failed"
    )
    assert claim(client, node).status_code == 204


def test_stopped_shared_model_does_not_starve_private_node(env):
    _, client, auth, _ = env
    assert (
        client.post(
            "/api/v1/admin/templates",
            headers=auth,
            json={
                "id": "chat",
                "name": "chat",
                "kind": "ai.chat",
                "worker": "synthetic.chat",
                "model": "static-review-chat",
                "public": True,
            },
        ).status_code
        == 201
    )
    shared, _, _ = register_node(client, auth, workers=["synthetic.chat"])
    _, private, _ = register_node(
        client, auth, mode="private", workers=["synthetic.chat"]
    )
    instance = next(
        i
        for i in client.get("/api/v1/admin/services", headers=auth).json()["items"]
        if i["node_id"] == shared["node_id"]
    )
    client.put(
        "/api/v1/admin/services/" + instance["id"],
        headers=auth,
        json={"desired_state": "stopped"},
    )
    assert (
        client.post(
            "/api/v1/jobs",
            headers=auth,
            json={
                "task_type": "ai.chat",
                "template_id": "chat",
                "input": {"messages": [{"role": "user", "content": "hi"}]},
            },
        ).status_code
        == 202
    )
    assert claim(client, private).status_code == 200


def test_removed_capability_still_accepts_stop_report(env):
    _, client, auth, _ = env
    assert (
        client.post(
            "/api/v1/admin/templates",
            headers=auth,
            json={
                "id": "chat",
                "name": "chat",
                "kind": "ai.chat",
                "worker": "synthetic.chat",
                "model": "report-chat",
                "public": True,
            },
        ).status_code
        == 201
    )
    _, node, caps = register_node(client, auth, workers=["synthetic.chat"])
    instance = client.get("/api/v1/admin/services", headers=auth).json()["items"][0]
    client.put(
        "/api/v1/admin/services/" + instance["id"],
        headers=auth,
        json={"desired_state": "ready"},
    )
    report = {
        "definition_id": "chat",
        "state": "starting",
        "active_requests": 1,
        "last_active": 0,
    }
    client.post(
        "/internal/v1/nodes/heartbeat",
        headers=node,
        json={"capabilities": caps, "policy": {"mode": "shared"}, "services": [report]},
    )
    caps["workers"] = []
    response = client.post(
        "/internal/v1/nodes/heartbeat",
        headers=node,
        json={
            "capabilities": caps,
            "policy": {"mode": "shared"},
            "services": [{**report, "state": "stopped", "active_requests": 0}],
        },
    )
    assert response.json()["services"][0]["desired_state"] == "stopped"
    row = client.get("/api/v1/admin/services", headers=auth).json()["items"][0]
    assert row["state"] == "stopped" and row["active_requests"] == 0


@pytest.mark.parametrize(
    "kind,data",
    [
        ("compute.stats", {"values": [10**400]}),
        ("ai.chat", {"messages": [{"role": [], "content": "hi"}]}),
        ("ai.image", {"prompt": "hi", "size": []}),
    ],
)
def test_malformed_nested_values_are_validation_errors(kind, data):
    from pcc_server.templates import validate_input

    with pytest.raises(HTTPException) as error:
        validate_input(SimpleNamespace(kind=kind), data)
    assert error.value.status_code == 422


def test_nonfinite_nested_json_is_a_validation_error():
    from pcc_server.jobs import fingerprint

    with pytest.raises(HTTPException) as error:
        fingerprint({"nested": [float("nan")]})
    assert error.value.status_code == 422


def test_windows_constructor_failure_cannot_claim_stop_confirmation(
    monkeypatch, tmp_path
):
    import pcc_agent.windows_sandbox as sandbox
    from pcc_agent.errors import WorkerStopUnconfirmed

    monkeypatch.setattr(sandbox, "api", lambda: (None, None, None))
    monkeypatch.setattr(sandbox, "runtime_root", lambda config: tmp_path)

    def failure(*args):
        raise OSError("Injected cleanup failure")

    monkeypatch.setattr(sandbox.WindowsWorker, "_start", failure)
    monkeypatch.setattr(sandbox.WindowsWorker, "close", failure)
    with pytest.raises(WorkerStopUnconfirmed):
        sandbox.WindowsWorker(SimpleNamespace(work_root=tmp_path), {})


def test_unconfirmed_constructor_retains_journal_and_never_reports_stopped(
    monkeypatch, tmp_path
):
    import pcc_agent.execution as execution
    from pcc_agent.config import AgentConfig
    from pcc_agent.errors import WorkerStopUnconfirmed

    requests = []

    class Transport:
        def post(self, path, json):
            requests.append(path)
            return httpx.Response(
                200,
                json={
                    "lease_expires_at": time.time() + 30,
                    "server_time": time.time(),
                    "cancel_requested": False,
                },
                request=httpx.Request("POST", "https://example.com" + path),
            )

    def failure(*args):
        raise WorkerStopUnconfirmed("Injected unconfirmed constructor")

    monkeypatch.setattr(execution, "SandboxedWorker", failure)
    agent = SimpleNamespace(
        config=AgentConfig(
            server="https://example.com",
            name="review",
            mode="shared",
            enable_compute=True,
            work_root=tmp_path,
        ),
        client=Transport(),
        heartbeat=lambda: None,
    )
    with pytest.raises(WorkerStopUnconfirmed):
        execution.execute(
            agent,
            {
                "job_id": "job",
                "attempt_id": "attempt",
                "lease_token": "lease",
                "deadline": time.time() + 120,
                "template": {"worker": "compute.stats"},
            },
        )
    assert (tmp_path / "active-attempt.json").exists()
    assert requests == ["/internal/v1/jobs/job/renew"]


def test_windows_probe_never_swallows_unconfirmed_cleanup(monkeypatch):
    import pcc_agent.windows_sandbox as sandbox
    from pcc_agent.errors import WorkerStopUnconfirmed

    def failure(*args):
        raise WorkerStopUnconfirmed("Injected uncertain probe cleanup")

    monkeypatch.setattr(sandbox, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(sandbox, "WindowsWorker", failure)
    with pytest.raises(WorkerStopUnconfirmed):
        sandbox.probe(SimpleNamespace(windows_worker_runtime="runtime"))


def test_restore_preflight_refuses_unrelated_existing_tables(monkeypatch, tmp_path):
    import pcc_server.backup as backup

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def scalar(self, query):
            return 1

    engine = SimpleNamespace(connect=Connection, dispose=lambda: None)
    monkeypatch.setattr(backup, "create_engine", lambda url: engine)
    with pytest.raises(ValueError, match="empty target database"):
        backup.check_restore_target(tmp_path, "postgresql://pcc@localhost/pcc")
