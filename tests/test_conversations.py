"""Durable chat, isolation, retries and administrator defaults. Author: FubukiHanasaki."""

import base64
import uuid
from fastapi.testclient import TestClient
from pcc_server.models import Job, ServiceDefinition
from test_auth import login


def model(c, auth, **extra):
    data = {
        "id": "chat",
        "name": "Qwen",
        "kind": "ai.chat",
        "worker": "ollama:qwen3.8:27b",
        "model": "qwen3.8:27b",
        "public": True,
        "parameters": {"vision": True},
        **extra,
    }
    r = c.post("/api/v1/admin/templates", headers=auth, json=data)
    assert r.status_code == 201, r.text
    return r


def send(c, auth, identity, text="你好", **extra):
    return c.post(
        f"/api/v1/conversations/{identity}/messages",
        headers=auth,
        json={"content": text, "request_id": uuid.uuid4().hex, **extra},
    )


def complete(app, job_id, content="你好，世界"):
    with app.state.sessions() as db:
        row = db.get(Job, job_id)
        row.status = "completed"
        row.result = {"content": content}
        db.commit()


def test_durable_roundtrip_history_and_retry(env):
    app, c, auth, _ = env
    model(c, auth)
    created = c.post(
        "/api/v1/conversations", headers=auth, json={"model": "qwen3.8:27b"}
    )
    assert created.status_code == 201
    identity = created.json()["id"]
    request_id = uuid.uuid4().hex
    first = send(c, auth, identity, request_id=request_id)
    assert first.status_code == 202, first.text
    assert send(c, auth, identity, request_id=request_id).json() == first.json()
    assert (
        send(c, auth, identity, "different", request_id=request_id).status_code == 409
    )
    assert send(c, auth, identity).status_code == 409
    complete(app, first.json()["job_id"])
    restored = c.get("/api/v1/conversations/" + identity, headers=auth).json()
    assert restored["turns"][0]["answer"] == "你好，世界"
    second = send(c, auth, identity, "记得我吗", parameters={"temperature": 0.5})
    assert second.status_code == 202
    with app.state.sessions() as db:
        job = db.get(Job, second.json()["job_id"])
        assert job.input["messages"] == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，世界"},
            {"role": "user", "content": "记得我吗"},
        ]
    assert (
        c.delete("/api/v1/conversations/" + identity, headers=auth).status_code == 409
    )
    complete(app, second.json()["job_id"])
    assert (
        c.patch(
            "/api/v1/conversations/" + identity,
            headers=auth,
            json={"title": "我的对话"},
        ).status_code
        == 200
    )
    assert (
        c.get("/api/v1/conversations", headers=auth).json()["items"][0]["title"]
        == "我的对话"
    )
    assert (
        c.delete("/api/v1/conversations/" + identity, headers=auth).status_code == 204
    )
    assert c.get("/api/v1/conversations/" + identity, headers=auth).status_code == 404


def test_conversations_are_never_visible_to_other_accounts(env):
    app, c, auth, _ = env
    model(c, auth)
    identity = c.post(
        "/api/v1/conversations", headers=auth, json={"model": "qwen3.8:27b"}
    ).json()["id"]
    c.post(
        "/api/v1/admin/users",
        headers=auth,
        json={"username": "visitor", "password": "visitor-test-password-123"},
    )
    with TestClient(app) as visitor:
        login(visitor, "visitor", "visitor-test-password-123")
        assert visitor.get("/api/v1/conversations").json()["items"] == []
        assert visitor.get("/api/v1/conversations/" + identity).status_code == 404
        # Missing CSRF is rejected before ownership checks.
        assert (
            visitor.post(
                "/api/v1/conversations/" + identity + "/messages",
                json={"content": "read", "request_id": "read"},
            ).status_code
            == 403
        )


def test_parameters_edit_synchronizes_service_and_blocks_active_work(env):
    app, c, auth, _ = env
    model(c, auth)
    body = {
        "name": "维护后的模型",
        "parameters": {
            "context_length": 16384,
            "max_tokens": 4096,
            "kv_cache_type": "q8_0",
            "vision": True,
        },
        "resources": {"cpu": 2, "memory_mb": 8192, "vram_mb": 1024},
        "max_timeout_seconds": 600,
        "idle_seconds": 60,
    }
    r = c.put("/api/v1/admin/templates/chat", headers=auth, json=body)
    assert r.status_code == 200, r.text
    m = c.get("/api/v1/models", headers=auth).json()["data"][0]
    assert m["parameters"]["context_length"] == 16384
    with app.state.sessions() as db:
        assert (
            db.get(ServiceDefinition, "chat").allowed_config["parameters"][
                "kv_cache_type"
            ]
            == "q8_0"
        )
    identity = c.post(
        "/api/v1/conversations", headers=auth, json={"model": m["id"]}
    ).json()["id"]
    assert (
        send(c, auth, identity, parameters={"context_length": 262144}).status_code
        == 422
    )
    assert send(c, auth, identity, parameters={"max_tokens": 4097}).status_code == 422
    assert send(c, auth, identity).status_code == 202
    assert (
        c.put("/api/v1/admin/templates/chat", headers=auth, json=body).status_code
        == 409
    )


def test_multimodal_inline_images_accepted_remote_paths_rejected(env):
    _, c, auth, _ = env
    model(c, auth)
    identity = c.post(
        "/api/v1/conversations", headers=auth, json={"model": "qwen3.8:27b"}
    ).json()["id"]
    url = (
        "data:image/png;base64,"
        + base64.b64encode(
            b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (1).to_bytes(4, "big") * 2
        ).decode()
    )
    for unsafe in [
        "https://127.0.0.1/private.png",
        "file:///etc/passwd",
        "data:image/png;base64,AAAA",
    ]:
        assert (
            send(
                c, auth, identity, [{"type": "image_url", "image_url": {"url": unsafe}}]
            ).status_code
            == 422
        )
    result = send(
        c,
        auth,
        identity,
        [
            {"type": "text", "text": "图片里是什么？"},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    )
    assert result.status_code == 202, result.text


def test_ninfer_backend_parameters_are_validated(env):
    _, c, auth, _ = env
    r = c.post(
        "/api/v1/admin/templates",
        headers=auth,
        json={
            "id": "ninfer",
            "name": "NInfer Qwen3.8",
            "kind": "ai.chat",
            "worker": "ninfer:qwen3.8-27b",
            "model": "qwen3.8-27b",
            "parameters": {"kv_cache_type": "fp8", "vision": True},
        },
    )
    assert r.status_code == 201, r.text
    assert (
        c.get("/api/v1/templates", headers=auth).json()["items"][0]["config"][
            "parameters"
        ]["kv_cache_type"]
        == "fp8"
    )


def test_admin_can_maintain_defaults_but_user_cannot(env):
    import time
    from pcc_server.models import APIKey, User
    from pcc_server.security import digest

    app, c, auth, _ = env
    model(c, auth)
    with app.state.sessions() as db:
        for role in ("admin", "user"):
            row = User(username=role, role=role)
            db.add(row)
            db.flush()
            db.add(
                APIKey(
                    user_id=row.id,
                    hash=digest(role + "-key", app.state.settings),
                    scopes=["admin"]
                    if role == "admin"
                    else ["jobs:read", "jobs:write"],
                    expires_at=time.time() + 3600,
                )
            )
        db.commit()
    body = {
        "name": "新默认值",
        "parameters": {"context_length": 16384},
        "resources": {"cpu": 2, "memory_mb": 512, "vram_mb": 0},
        "max_timeout_seconds": 120,
        "idle_seconds": 300,
    }
    path = "/api/v1/admin/templates/chat"
    assert (
        c.put(path, headers={"Authorization": "Bearer user-key"}, json=body).status_code
        == 403
    )
    assert (
        c.put(
            path, headers={"Authorization": "Bearer admin-key"}, json=body
        ).status_code
        == 200
    )
    assert (
        c.put(
            path,
            headers={"Authorization": "Bearer admin-key"},
            json={**body, "worker": "arbitrary"},
        ).status_code
        == 422
    )


def test_long_chinese_reply_persists_and_can_be_used_as_history(env):
    from conftest import register_node
    from test_jobs import claim, lease

    app, c, auth, _ = env
    model(c, auth)
    _, node, _ = register_node(c, auth, workers=["ollama:qwen3.8:27b"])
    identity = c.post(
        "/api/v1/conversations", headers=auth, json={"model": "qwen3.8:27b"}
    ).json()["id"]
    submitted = send(c, auth, identity).json()["job_id"]
    attempt = claim(c, node).json()
    assert attempt["job_id"] == submitted
    assert attempt["limits"]["output_bytes"] <= 256 * 1024
    for invalid in (None, {}, ["invalid"]):
        rejected = c.post(
            f"/internal/v1/jobs/{submitted}/complete",
            headers=node,
            json={**lease(attempt), "result": {"content": invalid}, "artifact_ids": []},
        )
        assert rejected.status_code == 422
    content = "这是较长的中文回复。" * 3000
    r = c.post(
        f"/internal/v1/jobs/{submitted}/complete",
        headers=node,
        json={**lease(attempt), "result": {"content": content}, "artifact_ids": []},
    )
    assert r.status_code == 200, r.text
    assert (
        c.get("/api/v1/conversations/" + identity, headers=auth).json()["turns"][0][
            "answer"
        ]
        == content
    )
    assert send(c, auth, identity, "继续").status_code == 202


def retry_fixture(env):
    app, c, auth, _ = env
    model(c, auth)
    identity = c.post(
        "/api/v1/conversations", headers=auth, json={"model": "qwen3.8:27b"}
    ).json()["id"]
    return app, c, auth, identity


def mark_failed(app, job_id, status="failed"):
    with app.state.sessions() as db:
        job = db.get(Job, job_id)
        job.status = status
        job.error = "test failure"
        db.commit()


def test_failed_reply_retry_preserves_history_and_both_idempotency_keys(env):
    app, c, auth, identity = retry_fixture(env)
    first = send(c, auth, identity).json()["job_id"]
    complete(app, first)
    original = send(
        c,
        auth,
        identity,
        "继续",
        request_id="original",
        parameters={"temperature": 0.5},
    )
    old_id = original.json()["job_id"]
    mark_failed(app, old_id)
    turn = c.get(f"/api/v1/conversations/{identity}", headers=auth).json()["turns"][-1]
    path = f"/api/v1/conversations/{identity}/turns/{turn['id']}/retry"
    retried = c.post(path, headers=auth, json={"request_id": "retry-1"})
    assert retried.status_code == 202, retried.text
    new_id = retried.json()["job_id"]
    assert new_id != old_id
    assert (
        c.post(path, headers=auth, json={"request_id": "retry-1"}).json()
        == retried.json()
    )
    assert c.post(path, headers=auth, json={"request_id": "retry-2"}).status_code == 409
    assert (
        send(
            c,
            auth,
            identity,
            "继续",
            request_id="original",
            parameters={"temperature": 0.5},
        ).json()
        == original.json()
    )
    assert send(c, auth, identity, "changed", request_id="original").status_code == 409
    with app.state.sessions() as db:
        assert db.get(Job, old_id).status == "failed"
        job = db.get(Job, new_id)
        assert job.input["messages"] == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，世界"},
            {"role": "user", "content": "继续"},
        ]
        assert job.input["parameters"]["temperature"] == 0.5
    detail = c.get(f"/api/v1/conversations/{identity}", headers=auth).json()
    assert len(detail["turns"]) == 2
    assert detail["turns"][-1]["job_id"] == new_id
    complete(app, new_id, "重试成功")
    assert c.post(path, headers=auth, json={"request_id": "retry-3"}).status_code == 409
    assert send(c, auth, identity, "下一句").status_code == 202
    assert (
        c.post(path, headers=auth, json={"request_id": "retry-1"}).json()
        == retried.json()
    )


def test_retry_rechecks_quota_parameters_and_rejects_other_turns(env):
    app, c, auth, identity = retry_fixture(env)
    job_id = send(c, auth, identity, parameters={"max_tokens": 2048}).json()["job_id"]
    mark_failed(app, job_id, "cancelled")
    turn = c.get(f"/api/v1/conversations/{identity}", headers=auth).json()["turns"][0]
    path = f"/api/v1/conversations/{identity}/turns/{turn['id']}/retry"
    app.state.settings.user_daily_job_limit = 1
    assert c.post(path, headers=auth, json={"request_id": "quota"}).status_code == 429
    app.state.settings.user_daily_job_limit = 1000
    from pcc_server.models import TaskTemplate

    with app.state.sessions() as db:
        template = db.get(TaskTemplate, "chat")
        template.config = {**template.config, "parameters": {"max_tokens": 1024}}
        db.commit()
    assert c.post(path, headers=auth, json={"request_id": "limits"}).status_code == 422
    assert send(c, auth, identity, "新消息").status_code == 202
    assert (
        c.post(path, headers=auth, json={"request_id": "old-turn"}).status_code == 409
    )
    assert (
        c.post(
            f"/api/v1/conversations/{identity}/turns/missing/retry",
            headers=auth,
            json={"request_id": "missing"},
        ).status_code
        == 404
    )


def test_retry_requires_ownership_csrf_and_current_model_authorization(env):
    import time
    from pcc_server.models import APIKey, TaskTemplate, User
    from pcc_server.security import digest

    app, c, auth, identity = retry_fixture(env)
    job_id = send(c, auth, identity).json()["job_id"]
    mark_failed(app, job_id)
    turn = c.get(f"/api/v1/conversations/{identity}", headers=auth).json()["turns"][0]
    path = f"/api/v1/conversations/{identity}/turns/{turn['id']}/retry"
    with app.state.sessions() as db:
        user = User(username="private", role="user")
        db.add(user)
        db.flush()
        db.add(
            APIKey(
                user_id=user.id,
                hash=digest("private-key", app.state.settings),
                scopes=["jobs:read", "jobs:write"],
                expires_at=time.time() + 3600,
            )
        )
        db.commit()
    assert (
        c.post(
            path,
            headers={"Authorization": "Bearer private-key"},
            json={"request_id": "other-user"},
        ).status_code
        == 404
    )
    login(c)
    assert c.post(path, json={"request_id": "csrf"}).status_code == 403
    with app.state.sessions() as db:
        # Revoke access without deleting a template referenced by historical jobs.
        db.get(TaskTemplate, "chat").public = False
        db.get(User, env[3]).role = "user"
        db.commit()
    assert c.post(path, headers=auth, json={"request_id": "revoked"}).status_code == 404


def test_concurrent_retry_creates_only_one_task(env):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import func, select

    app, c, auth, identity = retry_fixture(env)
    job_id = send(c, auth, identity).json()["job_id"]
    mark_failed(app, job_id)
    turn = c.get(f"/api/v1/conversations/{identity}", headers=auth).json()["turns"][0]
    path = f"/api/v1/conversations/{identity}/turns/{turn['id']}/retry"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: c.post(path, headers=auth, json={"request_id": "same"}),
                range(2),
            )
        )
    assert [r.status_code for r in results] == [202, 202]
    assert results[0].json() == results[1].json()
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 2


def test_retry_retains_multimodal_input_and_has_a_bounded_attempt_count(env):
    app, c, auth, identity = retry_fixture(env)
    image = (
        "data:image/png;base64,"
        + base64.b64encode(
            b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (1).to_bytes(4, "big") * 2
        ).decode()
    )
    content = [
        {"type": "text", "text": "这是什么？"},
        {"type": "image_url", "image_url": {"url": image}},
    ]
    job_id = send(c, auth, identity, content).json()["job_id"]
    mark_failed(app, job_id)
    turn = c.get(f"/api/v1/conversations/{identity}", headers=auth).json()["turns"][0]
    path = f"/api/v1/conversations/{identity}/turns/{turn['id']}/retry"
    for attempt in range(20):
        retried = c.post(path, headers=auth, json={"request_id": f"retry-{attempt}"})
        assert retried.status_code == 202, retried.text
        job_id = retried.json()["job_id"]
        with app.state.sessions() as db:
            assert db.get(Job, job_id).input["messages"] == [
                {"role": "user", "content": content}
            ]
        mark_failed(app, job_id)
    assert (
        c.post(path, headers=auth, json={"request_id": "over-limit"}).status_code == 422
    )
    assert (
        c.post(path, headers=auth, json={"request_id": "retry-19"}).json()["job_id"]
        == job_id
    )
