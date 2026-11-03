import time
from pcc_server.models import Node, RegistrationToken
from conftest import register_node


def test_one_time_registration_and_credential_separation(env):
    app, client, auth, _ = env
    token = client.post(
        "/api/v1/admin/registration-tokens", headers=auth, json={}
    ).json()["token"]
    payload = {"token": token, "name": "any-linux", "os": "Linux"}
    registered = client.post("/internal/v1/nodes/register", json=payload)
    assert registered.status_code == 201
    assert client.post("/internal/v1/nodes/register", json=payload).status_code == 401
    assert (
        client.get(
            "/api/v1/nodes",
            headers={"Authorization": "Bearer " + registered.json()["credential"]},
        ).status_code
        == 401
    )
    assert (
        client.post("/internal/v1/nodes/heartbeat", headers=auth, json={}).status_code
        == 401
    )
    with app.state.sessions() as db:
        assert db.query(RegistrationToken).first().hash != token


def test_expiration_metrics_stale_reconnect_revoke(env):
    app, client, auth, _ = env
    expired = client.post(
        "/api/v1/admin/registration-tokens", headers=auth, json={}
    ).json()["token"]
    with app.state.sessions() as db:
        row = db.query(RegistrationToken).first()
        row.expires_at = time.time() - 1
        db.commit()
    assert (
        client.post(
            "/internal/v1/nodes/register",
            json={"token": expired, "name": "expired", "os": "Windows"},
        ).status_code
        == 401
    )
    identity, headers, caps = register_node(client, auth)
    with app.state.sessions() as db:
        node = db.get(Node, identity["node_id"])
        node.last_seen = time.time() - 1000
        db.commit()
    assert (
        client.get("/api/v1/nodes", headers=auth).json()["items"][0]["status"]
        == "offline"
    )
    response = client.post(
        "/internal/v1/nodes/heartbeat",
        headers=headers,
        json={"capabilities": caps, "policy": {"mode": "private"}},
    )
    assert response.status_code == 200
    assert (
        client.get("/api/v1/nodes", headers=auth).json()["items"][0]["status"]
        == "online"
    )
    client.post(f"/api/v1/nodes/{identity['node_id']}/revoke", headers=auth)
    assert (
        client.post(
            "/internal/v1/nodes/heartbeat",
            headers=headers,
            json={"capabilities": caps, "policy": {}},
        ).status_code
        == 401
    )
