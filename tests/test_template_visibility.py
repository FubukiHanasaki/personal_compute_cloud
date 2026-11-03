"""Authorization UI backing contract. Author: FubukiHanasaki."""

from pcc_server.models import APIKey, User
from pcc_server.security import digest, hash_password


def test_visibility_controls_model_access_without_changing_worker(env):
    app, client, auth, _ = env
    body = {
        "id": "local-chat",
        "name": "本地 Chat",
        "kind": "ai.chat",
        "worker": "ollama:qwen3:8b",
        "model": "qwen3:8b",
        "public": False,
    }
    assert (
        client.post("/api/v1/admin/templates", headers=auth, json=body).status_code
        == 201
    )
    with app.state.sessions() as db:
        user = User(
            username="viewer",
            password_hash=hash_password("test-user-password"),
            role="user",
        )
        admin = User(
            username="admin",
            password_hash=hash_password("test-admin-password"),
            role="admin",
        )
        db.add_all([user, admin])
        db.flush()
        for row, key, scopes in (
            (user, "viewer-key", ["models:read", "jobs:read"]),
            (admin, "admin-key", ["admin"]),
        ):
            db.add(
                APIKey(
                    user_id=row.id,
                    hash=digest(key, app.state.settings),
                    scopes=scopes,
                    expires_at=9999999999,
                )
            )
        db.commit()
    viewer = {"Authorization": "Bearer viewer-key"}
    path = "/api/v1/admin/templates/local-chat/visibility"
    assert client.get("/api/v1/models", headers=viewer).json()["data"] == []
    assert client.put(path, headers=viewer, json={"public": True}).status_code == 403
    assert (
        client.put(
            path, headers={"Authorization": "Bearer admin-key"}, json={"public": True}
        ).status_code
        == 403
    )
    assert (
        client.put(
            path, headers=auth, json={"public": True, "worker": "arbitrary"}
        ).status_code
        == 422
    )
    assert client.put(path, headers=auth, json={"public": True}).status_code == 200
    assert (
        client.get("/api/v1/models", headers=viewer).json()["data"][0]["id"]
        == "qwen3:8b"
    )
    template = client.get("/api/v1/templates", headers=auth).json()["items"][0]
    assert template["public"] and template["worker"] == body["worker"]
    assert client.put(path, headers=auth, json={"public": False}).status_code == 200
    assert client.get("/api/v1/models", headers=viewer).json()["data"] == []
