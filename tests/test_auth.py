from fastapi.testclient import TestClient
from pcc_server.models import AuditEvent
from conftest import register_node
from test_jobs import template, submit, claim


def login(client, username="Fubuki", password="test-password-0123456"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_session_csrf_key_scopes_and_revocation(env):
    app, client, auth, _ = env
    csrf = login(client)
    assert "httponly" in client.cookies.jar._cookies["testserver.local"]["/"]["pcc_session"]._rest.keys() or client.cookies.get("pcc_session")
    assert client.post("/api/v1/keys", json={"scopes": ["jobs:read"]}).status_code == 403
    issued = client.post("/api/v1/keys", headers=csrf, json={"scopes": ["jobs:read"]}).json()
    headers = {"Authorization": "Bearer "+issued["key"]}
    assert client.get("/api/v1/jobs", headers=headers).status_code == 200
    assert client.post("/api/v1/admin/registration-tokens", headers=headers, json={}).status_code == 403
    assert client.post("/api/v1/keys", headers=headers, json={"scopes": ["admin"]}).status_code == 403
    assert client.post(f"/api/v1/keys/{issued['id']}/revoke", headers=csrf).status_code == 204
    assert client.get("/api/v1/jobs", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/logout", headers=csrf).status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_users_cannot_access_others_or_escalate(env):
    app, c, auth, _ = env
    template(c, auth)
    alice = c.post("/api/v1/admin/users", headers=auth, json={"username": "alice", "password": "alice-password-123456"}).json()
    bob = c.post("/api/v1/admin/users", headers=auth, json={"username": "bob", "password": "bob-password-12345678"}).json()
    with TestClient(app) as a, TestClient(app) as b:
        ah = login(a, "alice", "alice-password-123456")
        bh = login(b, "bob", "bob-password-12345678")
        jid = submit(a, ah).json()["job_id"]
        assert b.get(f"/api/v1/jobs/{jid}").status_code == 404
        assert b.get(f"/api/v1/jobs/{jid}/artifacts").status_code == 404
        assert b.post(f"/api/v1/jobs/{jid}/cancel", headers=bh).status_code == 404
        assert b.get("/api/v1/jobs").json()["items"] == []
        assert b.post("/api/v1/keys", headers=bh, json={"scopes": ["admin"]}).status_code == 403
        assert b.post("/api/v1/admin/users", headers=bh, json={"username": "eve", "password": "password-0123456789"}).status_code == 403
        assert b.get("/api/v1/nodes").json()["items"] == []


def test_private_local_policy_and_admin_grants(env):
    app, c, auth, _ = env
    template(c, auth)
    identity, nh, caps = register_node(c, auth, mode="private")
    c.post("/api/v1/admin/users", headers=auth, json={"username": "user", "password": "password-0123456789"})
    delegated = c.post("/api/v1/admin/users", headers=auth, json={"username": "admin", "password": "password-0123456789", "role": "admin"}).json()
    with TestClient(app) as u:
        uh = login(u, "user", "password-0123456789")
        submit(u, uh)
    assert claim(c, nh).status_code == 204
    with TestClient(app) as a:
        ah = login(a, "admin", "password-0123456789")
        path = f"/api/v1/nodes/{identity['node_id']}/policy"
        assert a.put(path, headers=ah, json={"mode": "shared"}).status_code == 404
        c.put(f"/api/v1/admin/users/{delegated['id']}", headers=auth, json={"grants": {"nodes": [identity["node_id"]]}})
        assert a.put(path, headers=ah, json={"mode": "shared"}).status_code == 200
    # Local private still wins over cloud shared.
    assert claim(c, nh).status_code == 204
    c.post("/internal/v1/nodes/heartbeat", headers=nh, json={"capabilities": caps, "policy": {"mode": "shared"}})
    assert claim(c, nh).status_code == 200


def test_login_throttle_and_audit(env):
    app, c, auth, _ = env
    for _ in range(10):
        assert c.post("/api/v1/auth/login", json={"username": "nobody", "password": "bad"}).status_code == 401
    assert c.post("/api/v1/auth/login", json={"username": "nobody", "password": "bad"}).status_code == 429
    audit = c.get("/api/v1/admin/audit", headers=auth).json()["items"]
    assert any(a["action"] == "auth.login" and a["outcome"] == "denied" for a in audit)
