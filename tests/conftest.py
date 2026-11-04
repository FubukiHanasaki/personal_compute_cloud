import time
import os
import pytest
from fastapi.testclient import TestClient
from pcc_server.app import create_app
from pcc_server.config import Settings
from pcc_server.db import Base
from pcc_server.models import User, APIKey
from pcc_server.security import digest, hash_password


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        secret="test-only-" * 5,
        environment="development",
        secure_cookies=False,
        database_url=os.environ.get(
            "PCC_TEST_DATABASE_URL", f"sqlite:///{tmp_path}/test.sqlite"
        ),
        storage_root=tmp_path / "artifacts",
    )
    app = create_app(settings)
    if os.environ.get("PCC_TEST_DATABASE_URL"):
        Base.metadata.drop_all(app.state.engine)
    Base.metadata.create_all(app.state.engine)
    with app.state.sessions() as db:
        owner = User(
            username="Fubuki",
            role="owner",
            password_hash=hash_password("test-password-0123456"),
        )
        db.add(owner)
        db.flush()
        owner_id = owner.id
        db.add(
            APIKey(
                user_id=owner.id,
                hash=digest("owner-key", settings),
                scopes=["admin"],
                expires_at=time.time() + 3600,
            )
        )
        db.commit()
    with TestClient(app) as client:
        yield app, client, {"Authorization": "Bearer owner-key"}, owner_id
    app.state.engine.dispose()


def register_node(client, auth, mode="shared", workers=None):
    reg = client.post(
        "/api/v1/admin/registration-tokens", headers=auth, json={}
    ).json()["token"]
    identity = client.post(
        "/internal/v1/nodes/register",
        json={"token": reg, "name": "arbitrary-device", "os": "Linux"},
    ).json()
    headers = {"Authorization": "Bearer " + identity["credential"]}
    caps = {
        "physical": {"cpu": 8, "memory_mb": 8192, "vram_mb": 4096},
        "allocatable": {"cpu": 2, "memory_mb": 2048, "vram_mb": 4096},
        "available": {"cpu": 6, "memory_mb": 4096, "vram_mb": 4096},
        "workers": workers or ["compute.stats"],
        "isolation": ["linux-bwrap-rlimit"],
    }
    caps["managed_commit_budget_mb"] = 32768
    caps["managed_available_memory_mb"] = {
        worker: 1024
        for worker in caps["workers"]
        if worker.startswith(("ollama:", "comfyui:", "ninfer:"))
    }
    assert (
        client.post(
            "/internal/v1/nodes/heartbeat",
            headers=headers,
            json={"capabilities": caps, "policy": {"mode": mode}},
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/nodes/{identity['node_id']}/policy",
            headers=auth,
            json={"mode": mode},
        ).status_code
        == 200
    )
    return identity, headers, caps
