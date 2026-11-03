import base64
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pcc_server.models import Job, JobAttempt, Node
from conftest import register_node


def template(client, auth, retry_safe=True):
    assert (
        client.post(
            "/api/v1/admin/templates",
            headers=auth,
            json={
                "id": "stats",
                "name": "统计",
                "kind": "compute.stats",
                "worker": "compute.stats",
                "public": True,
                "retry_safe": retry_safe,
            },
        ).status_code
        == 201
    )


def submit(client, auth, **kwargs):
    return client.post(
        "/api/v1/jobs",
        headers=auth,
        json={
            "task_type": "compute.stats",
            "template_id": "stats",
            "input": {"values": [1, 2, 3]},
            **kwargs,
        },
    )


def claim(client, headers, claim_id="x" * 32):
    return client.post(
        "/internal/v1/jobs/claim", headers=headers, json={"claim_id": claim_id}
    )


def lease(payload):
    return {k: payload[k] for k in ("attempt_id", "lease_token")}


def test_history_type_filter_precedes_pagination_and_preserves_ownership(env):
    """Old image history remains reachable after newer compute jobs. Author: Fubuki."""
    from fastapi.testclient import TestClient
    from test_auth import login

    app, c, auth, owner_id = env
    template(c, auth)
    user = c.post(
        "/api/v1/admin/users",
        headers=auth,
        json={"username": "history-user", "password": "history-password-1234"},
    ).json()
    with app.state.sessions() as db:
        for i in range(65):
            db.add(
                Job(
                    owner_id=user["id"] if i < 64 else owner_id,
                    template_id="stats",
                    task_type="ai.image" if i < 32 or i == 64 else "compute.stats",
                    status="completed",
                    input={},
                    requirements={},
                    timeout_seconds=60,
                    request_hash="fixture",
                    created_at=1000 + i,
                )
            )
        db.commit()
    with TestClient(app) as client:
        login(client, "history-user", "history-password-1234")
        assert all(
            row["task_type"] == "compute.stats"
            for row in client.get("/api/v1/jobs?limit=30").json()["items"]
        )
        first = client.get("/api/v1/jobs?task_type=ai.image&limit=30").json()["items"]
        second = client.get(
            "/api/v1/jobs?task_type=ai.image&limit=30&offset=30"
        ).json()["items"]
        assert len(first) == 30 and len(second) == 2
        assert all(row["task_type"] == "ai.image" for row in first + second)
        assert len({row["job_id"] for row in first + second}) == 32
        assert client.get("/api/v1/jobs?task_type=shell").status_code == 422
    assert (
        len(
            c.get("/api/v1/jobs?task_type=ai.image&limit=100", headers=auth).json()[
                "items"
            ]
        )
        == 33
    )


def test_job_lifecycle_artifacts_idempotency_and_logs(env):
    app, c, auth, _ = env
    template(c, auth)
    identity, node, _ = register_node(c, auth)
    idem = {**auth, "Idempotency-Key": "first"}
    job = submit(c, idem).json()
    assert submit(c, idem).json()["job_id"] == job["job_id"]
    assert submit(c, idem, input={"values": [4]}).status_code == 409
    attempt = claim(c, node).json()
    repeated = claim(c, node).json()
    assert (repeated["attempt_id"], repeated["lease_token"]) == (
        attempt["attempt_id"],
        attempt["lease_token"],
    )
    jid = job["job_id"]
    assert (
        c.post(
            f"/internal/v1/jobs/{jid}/renew", headers=node, json=lease(attempt)
        ).json()["status"]
        == "running"
    )
    event = {**lease(attempt), "sequence": 1, "message": "working", "progress": 0.5}
    for _ in range(2):
        assert (
            c.post(
                f"/internal/v1/jobs/{jid}/events", headers=node, json=event
            ).status_code
            == 200
        )
    data = b'{"sum":6}'
    upload = {
        **lease(attempt),
        "name": "result.json",
        "data_base64": base64.b64encode(data).decode(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    uploaded = c.post(f"/internal/v1/jobs/{jid}/artifacts", headers=node, json=upload)
    assert uploaded.status_code == 201
    artifact_id = uploaded.json()["artifact_id"]
    assert (
        c.post(
            f"/internal/v1/jobs/{jid}/artifacts",
            headers=node,
            json={**upload, "name": "../secret"},
        ).status_code
        == 422
    )
    done = {**lease(attempt), "result": {"sum": 6}, "artifact_ids": [artifact_id]}
    for _ in range(2):
        assert (
            c.post(f"/internal/v1/jobs/{jid}/complete", headers=node, json=done).json()[
                "status"
            ]
            == "completed"
        )
    assert (
        c.post(
            f"/internal/v1/jobs/{jid}/complete",
            headers=node,
            json={**done, "result": {"sum": 7}},
        ).status_code
        == 409
    )
    assert c.get(f"/api/v1/artifacts/{artifact_id}", headers=auth).content == data
    assert len(c.get(f"/api/v1/jobs/{jid}/events", headers=auth).json()["items"]) == 1


def test_expired_lease_no_parallel_retry_late_completion(env):
    app, c, auth, _ = env
    template(c, auth)
    _, node, _ = register_node(c, auth)
    jid = submit(c, auth).json()["job_id"]
    old = claim(c, node).json()
    with app.state.sessions() as db:
        db.get(JobAttempt, old["attempt_id"]).lease_expires_at = time.time() - 1
        db.commit()
    assert c.get(f"/api/v1/jobs/{jid}", headers=auth).json()["status"] == "reconciling"
    assert claim(c, node, "y" * 32).status_code == 204
    assert (
        c.post(
            f"/internal/v1/jobs/{jid}/complete",
            headers=node,
            json={**lease(old), "result": {}},
        ).status_code
        == 409
    )
    assert (
        c.post(
            f"/internal/v1/jobs/{jid}/fail",
            headers=node,
            json={**lease(old), "stopped": False, "message": "lost"},
        ).status_code
        == 409
    )
    stopped = c.post(
        f"/internal/v1/jobs/{jid}/fail",
        headers=node,
        json={
            **lease(old),
            "stopped": True,
            "retryable": True,
            "message": "confirmed stopped",
        },
    )
    assert stopped.json()["status"] == "queued"
    new = claim(c, node, "z" * 32).json()
    assert new["attempt_id"] != old["attempt_id"]
    assert (
        c.post(
            f"/internal/v1/jobs/{jid}/complete",
            headers=node,
            json={**lease(old), "result": {}},
        ).status_code
        == 409
    )


def test_cancel_and_concurrent_claim(env):
    app, c, auth, _ = env
    template(c, auth)
    _, node1, _ = register_node(c, auth)
    _, node2, _ = register_node(c, auth)
    jid = submit(c, auth).json()["job_id"]
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda h: claim(c, h), [node1, node2]))
    assert sorted(r.status_code for r in results) == [200, 204]
    index = next(i for i, r in enumerate(results) if r.status_code == 200)
    node = [node1, node2][index]
    active = results[index].json()
    assert (
        c.post(f"/api/v1/jobs/{jid}/cancel", headers=auth).json()["status"]
        == "cancel_requested"
    )
    assert c.post(
        f"/internal/v1/jobs/{jid}/renew", headers=node, json=lease(active)
    ).json()["cancel_requested"]
    assert (
        c.post(
            f"/internal/v1/jobs/{jid}/fail",
            headers=node,
            json={**lease(active), "stopped": True, "message": "cancel"},
        ).json()["status"]
        == "cancelled"
    )


def test_untrusted_input_capabilities_and_protection(env):
    app, c, auth, _ = env
    template(c, auth)
    identity, node, caps = register_node(c, auth)
    assert submit(c, auth, input={"command": "id"}).status_code == 422
    assert submit(c, auth, template_id="shell").status_code == 403
    assert submit(c, auth, allow_paid_cloud=True).status_code == 422
    submit(c, auth)
    caps["workers"] = []
    c.post(
        "/internal/v1/nodes/heartbeat",
        headers=node,
        json={"capabilities": caps, "policy": {"mode": "shared"}},
    )
    assert claim(c, node).status_code == 204
    caps["workers"] = ["compute.stats"]
    caps["cpu_load"] = 99
    c.post(
        "/internal/v1/nodes/heartbeat",
        headers=node,
        json={"capabilities": caps, "policy": {"mode": "shared"}},
    )
    assert claim(c, node).status_code == 204


def test_shared_preference_and_non_idempotent_failure(env):
    app, c, auth, _ = env
    template(c, auth, retry_safe=False)
    _, private, _ = register_node(c, auth, mode="private")
    _, shared, _ = register_node(c, auth, mode="shared")
    jid = submit(c, auth).json()["job_id"]
    assert claim(c, private).status_code == 204
    active = claim(c, shared).json()
    failed = c.post(
        f"/internal/v1/jobs/{jid}/fail",
        headers=shared,
        json={
            **lease(active),
            "stopped": True,
            "retryable": True,
            "message": "No automatic retry for side effects",
        },
    )
    assert failed.json()["status"] == "failed"
    assert claim(c, shared, "next" * 8).status_code == 204


def test_concurrent_idempotent_submission(env):
    app, c, auth, _ = env
    template(c, auth)
    headers = {**auth, "Idempotency-Key": "same-owner-same-request"}
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: submit(c, headers), range(2)))
    assert all(r.status_code == 202 for r in results)
    assert len({r.json()["job_id"] for r in results}) == 1
