"""Nonempty passwords of any complexity are accepted. Author: FubukiHanasaki."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from pcc_server.cli import bootstrap_owner
from pcc_server.config import Settings
from pcc_server.db import Base, make_engine, session_factory
from pcc_server.models import User
from pcc_server.security import verify_password
from test_auth import login


@pytest.mark.parametrize("password", ["1", "123456", "aaaaaa", "密码", "p" * 256])
def test_create_login_and_change_simple_passwords(env, password):
    app, client, auth, _ = env
    created = client.post(
        "/api/v1/admin/users",
        headers=auth,
        json={"username": "simple-user", "password": password},
    )
    assert created.status_code == 201, created.text
    with TestClient(app) as user:
        csrf = login(user, "simple-user", password)
        # A simple password still requires the correct current password and CSRF.
        assert (
            user.post(
                "/api/v1/auth/password",
                json={"current_password": password, "new_password": "2"},
            ).status_code
            == 403
        )
        assert (
            user.post(
                "/api/v1/auth/password",
                headers=csrf,
                json={"current_password": "wrong-current", "new_password": "2"},
            ).status_code
            == 403
        )
        changed = user.post(
            "/api/v1/auth/password",
            headers=csrf,
            json={"current_password": password, "new_password": "2"},
        )
        assert changed.status_code == 204, changed.text
        assert user.get("/api/v1/auth/me").status_code == 401
        assert (
            user.post(
                "/api/v1/auth/login",
                json={"username": "simple-user", "password": password},
            ).status_code
            == 401
        )
        login(user, "simple-user", "2")
    with app.state.sessions() as db:
        row = db.scalar(select(User).where(User.username == "simple-user"))
        assert row.password_hash != "2"
        assert verify_password("2", row.password_hash)


@pytest.mark.parametrize("password", ["", "p" * 257])
def test_empty_and_oversized_passwords_remain_invalid(env, password):
    _, client, auth, _ = env
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=auth,
            json={"username": "invalid-user", "password": password},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/password",
            headers=auth,
            json={
                "current_password": "test-password-0123456",
                "new_password": password,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "Fubuki", "password": password}
        ).status_code
        == 422
    )
    login(client)


@pytest.mark.parametrize("password", ["1", "123456", "aaaaaa", "密码"])
def test_owner_bootstrap_accepts_simple_password_and_keeps_existing_owner(
    tmp_path, password
):
    settings = Settings(
        secret="password-policy-test-" * 3,
        environment="development",
        secure_cookies=False,
        database_url=f"sqlite:///{tmp_path / 'owner.sqlite'}",
        owner_password=password,
        owner_username="owner",
    )
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        with session_factory(engine)() as db:
            bootstrap_owner(db, settings)
            owner = db.scalar(select(User).where(User.role == "owner"))
            assert verify_password(password, owner.password_hash)
            stored = owner.password_hash
            settings.owner_password = "different"
            bootstrap_owner(db, settings)
            assert db.get(User, owner.id).password_hash == stored
    finally:
        engine.dispose()
