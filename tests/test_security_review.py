"""Security regressions prepared for later execution. Author: Fubuki."""

import asyncio
from types import SimpleNamespace
import tracemalloc
import pytest
from fastapi.testclient import TestClient
from pcc_agent.errors import WorkerStopUnconfirmed
from pcc_server.models import User
from test_auth import login


@pytest.mark.parametrize(
    "scope", ["jobs:read", "jobs:write", "nodes:read", "models:read"]
)
def test_application_scopes_cannot_manage_account_credentials(env, scope):
    _, client, auth, _ = env
    privileged = client.post(
        "/api/v1/keys", headers=auth, json={"scopes": ["admin"]}
    ).json()
    restricted = client.post(
        "/api/v1/keys", headers=auth, json={"scopes": [scope]}
    ).json()
    headers = {"Authorization": "Bearer " + restricted["key"]}
    assert client.get("/api/v1/keys", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/keys", headers=headers, json={"scopes": [scope]}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/keys/{privileged['id']}/revoke", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/keys", headers={"Authorization": "Bearer " + privileged["key"]}
        ).status_code
        == 200
    )


def test_disable_invalidates_existing_keys_sessions_and_registration_tokens(env):
    app, client, auth, _ = env
    user = client.post(
        "/api/v1/admin/users",
        headers=auth,
        json={
            "username": "delegated",
            "password": "delegated-password-1234",
            "role": "admin",
        },
    ).json()
    with TestClient(app) as delegated:
        csrf = login(delegated, "delegated", "delegated-password-1234")
        key = delegated.post(
            "/api/v1/keys", headers=csrf, json={"scopes": ["admin"]}
        ).json()
        token = delegated.post(
            "/api/v1/admin/registration-tokens", headers=csrf, json={}
        ).json()["token"]
        path = "/api/v1/admin/users/" + user["id"]
        assert (
            client.put(path, headers=auth, json={"status": "disabled"}).status_code
            == 200
        )
        assert (
            client.put(path, headers=auth, json={"status": "active"}).status_code == 200
        )
        assert delegated.get("/api/v1/auth/me").status_code == 401
        assert (
            client.get(
                "/api/v1/keys", headers={"Authorization": "Bearer " + key["key"]}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/internal/v1/nodes/register",
                json={
                    "token": token,
                    "name": "revoked-issuer",
                    "os": "Windows",
                },
            ).status_code
            == 401
        )


def test_registration_rechecks_issuer_even_without_revocation_sweep(env):
    app, client, auth, owner = env
    token = client.post(
        "/api/v1/admin/registration-tokens", headers=auth, json={}
    ).json()["token"]
    with app.state.sessions() as db:
        db.get(User, owner).status = "disabled"
        db.commit()
    assert (
        client.post(
            "/internal/v1/nodes/register",
            json={
                "token": token,
                "name": "blocked",
                "os": "Linux",
            },
        ).status_code
        == 401
    )


def test_non_ascii_csrf_is_rejected_as_forbidden(env):
    _, client, _, _ = env
    login(client)
    response = client.post(
        "/api/v1/keys",
        headers=[(b"x-csrf-token", b"\xff")],
        json={"scopes": ["jobs:read"]},
    )
    assert response.status_code == 403


def test_fragmented_request_buffer_has_a_byte_proportional_memory_bound():
    from pcc_server.middleware import BodyLimitMiddleware

    if tracemalloc.is_tracing():
        pytest.skip("Requires isolated allocation measurement")
    size, received = 65536, 0

    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": b"x", "more_body": received < size}

    async def app(scope, read, send):
        assert (await read())["body"] == b"x" * size

    async def send(event):
        raise AssertionError("Body at limit must reach application")

    scope = {"type": "http", "method": "POST", "headers": []}
    tracemalloc.start()
    try:
        asyncio.run(BodyLimitMiddleware(app, size)(scope, receive, send))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert received == size
    assert peak < 1024 * 1024, "Tiny chunks must not amplify the memory bound"


def test_linux_constructor_failure_stops_partial_worker_before_propagation(monkeypatch):
    from pcc_agent.sandbox import SandboxedWorker

    stopped = []

    def start(self, config, payload):
        self.process = SimpleNamespace(stdin=None)
        raise BrokenPipeError("Injected write failure after spawn")

    monkeypatch.setattr(SandboxedWorker, "_start", start)
    monkeypatch.setattr(SandboxedWorker, "stop", lambda self: stopped.append(True))
    with pytest.raises(BrokenPipeError):
        SandboxedWorker.__init__(object.__new__(SandboxedWorker), None, {})
    assert stopped == [True]


def test_linux_constructor_unconfirmed_cleanup_aborts_execution(monkeypatch):
    from pcc_agent.sandbox import SandboxedWorker

    def failure(*args):
        raise OSError("Injected unconfirmed cleanup")

    monkeypatch.setattr(SandboxedWorker, "_start", failure)
    monkeypatch.setattr(SandboxedWorker, "close", failure)
    with pytest.raises(WorkerStopUnconfirmed):
        SandboxedWorker.__init__(object.__new__(SandboxedWorker), None, {})
