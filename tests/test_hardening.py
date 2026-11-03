import base64
import hashlib
import time
import pytest
from pcc_server.models import Artifact, JobAttempt
from pcc_server.maintenance import cleanup
from pcc_server.storage import LocalStorage
from pcc_server.config import Settings
from conftest import register_node
from test_jobs import template, submit, claim, lease


def test_limits_errors_schema_audit_and_cleanup(env):
    app, c, auth, _ = env
    template(c, auth)
    _, nh, _ = register_node(c, auth)
    app.state.settings.user_active_job_limit = 1
    jid = submit(c, auth).json()["job_id"]
    assert submit(c, auth).status_code == 429
    payload = claim(c, nh).json()
    data = b"checksum"
    body = {
        **lease(payload),
        "name": "result.bin",
        "data_base64": base64.b64encode(data).decode(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    bad = {**body, "sha256": "0" * 64}
    assert (
        c.post(f"/internal/v1/jobs/{jid}/artifacts", headers=nh, json=bad).status_code
        == 422
    )
    artifact = c.post(
        f"/internal/v1/jobs/{jid}/artifacts", headers=nh, json=body
    ).json()
    with app.state.sessions() as db:
        art = db.get(Artifact, artifact["artifact_id"])
        art.expires_at = time.time() - 1
        key = art.key
        db.commit()
        assert cleanup(db, app.state.settings)["removed"] == 1
    assert not LocalStorage(app.state.settings.storage_root).path(key).exists()
    assert (
        c.get("/api/v1/artifacts/" + artifact["artifact_id"], headers=auth).status_code
        == 404
    )
    assert (
        c.post("/api/v1/jobs", headers=auth, json={"command": "sh"}).status_code == 422
    )
    events = c.get("/api/v1/admin/audit", headers=auth).json()["items"]
    assert any(e["action"] == "http.rejected" for e in events)
    assert "/internal/v1/jobs/claim" in c.get("/openapi.json").json()["paths"]


def test_chunked_body_limit_and_generic_error(env):
    app, c, auth, _ = env
    # Header-only rejection avoids allocating a giant body in a test.
    response = c.post(
        "/api/v1/auth/login", content=b"{}", headers={"Content-Length": "999999999"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["request_id"]

    @app.get("/test-crash")
    def crash():
        raise RuntimeError("sensitive-server-detail")

    response = c.get("/test-crash")
    assert response.status_code == 500
    assert "sensitive-server-detail" not in response.text
    assert response.json()["error"]["code"] == "internal_error"


def test_operator_reconciliation_fences_old_result(env):
    app, c, auth, _ = env
    template(c, auth)
    _, nh, _ = register_node(c, auth)
    jid = submit(c, auth).json()["job_id"]
    payload = claim(c, nh).json()
    with app.state.sessions() as db:
        db.get(JobAttempt, payload["attempt_id"]).lease_expires_at = time.time() - 1
        db.commit()
    path = f"/api/v1/admin/jobs/{jid}/reconcile"
    assert (
        c.post(
            path,
            headers=auth,
            json={"confirmed_stopped": False, "evidence": "insufficient evidence"},
        ).status_code
        == 409
    )
    assert (
        c.post(
            path,
            headers=auth,
            json={
                "confirmed_stopped": True,
                "retry": True,
                "evidence": "operator inspected exited process group",
            },
        ).json()["status"]
        == "queued"
    )
    assert (
        c.post(
            f"/internal/v1/jobs/{jid}/complete",
            headers=nh,
            json={**lease(payload), "result": {}},
        ).status_code
        == 409
    )


def test_no_insecure_production_configuration():
    with pytest.raises(ValueError):
        Settings(
            secret="x" * 40,
            environment="production",
            database_url="postgresql+psycopg://pcc:REPLACE_ME@db/pcc",
        )
    with pytest.raises(ValueError):
        Settings(secret="x" * 40, environment="production", database_url="sqlite://")
    with pytest.raises(ValueError):
        Settings(
            secret="REPLACE_WITH_RANDOM_64_HEX_CHARACTERS", environment="development"
        )
