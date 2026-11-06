"""Native generic inference contract tests; no model weights loaded. FubukiHanasaki."""

import base64
import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, func

from conftest import register_node
from test_jobs import claim, lease
from pcc_server.models import Job, APIKey, TaskTemplate, Artifact
from pcc_server.security import digest

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5ioAAAAASUVORK5CYII="
)


def setup_model(env, kind="chat", **extra):
    app, c, auth, _ = env
    app.state.settings.chat_wait_seconds = 5
    app.state.settings.image_wait_seconds = 5
    adapter = "ollama" if kind == "chat" else "comfyui"
    model = "native-" + kind
    data = dict(
        id=model,
        name=model,
        model=model,
        kind="ai." + kind,
        worker=adapter + ":" + model,
        **extra,
    )
    result = c.post("/api/v1/admin/templates", headers=auth, json=data)
    assert result.status_code == 201, result.text
    _, nh, _ = register_node(c, auth, workers=[data["worker"]])
    return model, nh


def poll(c, nh):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        res = claim(c, nh, uuid.uuid4().hex)
        if res.status_code == 200:
            return res.json()
        assert res.status_code == 204, res.text
        time.sleep(0.02)
    pytest.fail("native request was not claimable")


def complete(c, nh, attempt, result, image=False):
    jid = attempt["job_id"]
    artifacts = []
    if image:
        res = c.post(
            f"/internal/v1/jobs/{jid}/artifacts",
            headers=nh,
            json={
                **lease(attempt),
                "name": "image.png",
                "data_base64": base64.b64encode(PNG).decode(),
                "sha256": hashlib.sha256(PNG).hexdigest(),
            },
        )
        assert res.status_code == 201, res.text
        artifacts.append(res.json()["artifact_id"])
    res = c.post(
        f"/internal/v1/jobs/{jid}/complete",
        headers=nh,
        json={**lease(attempt), "result": result, "artifact_ids": artifacts},
    )
    assert res.status_code == 200, res.text


def test_native_model_list_alias_and_kind(env):
    _, c, auth, _ = env
    setup_model(env, "chat")
    setup_model(env, "image")
    assert (
        c.get("/v1/models", headers=auth).json()
        == c.get("/api/v1/models", headers=auth).json()
    )
    for kind, model in [("text", "native-chat"), ("image", "native-image")]:
        r = c.get("/v1/models", headers=auth, params={"kind": kind}).json()
        assert r["object"] == "list"
        assert [m["id"] for m in r["data"]] == [model]
        assert r["data"][0]["kind"] == kind
    assert c.get("/v1/models?kind=other", headers=auth).status_code == 422
    assert c.get("/v1/models").status_code == 401
    with env[0].state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.parametrize("stream", [False, True])
def test_native_text_raw_messages_and_top_level_sampling(env, stream):
    _, c, auth, _ = env
    model, nh = setup_model(env, parameters={"max_tokens": 100})
    messages = [
        {"role": "system", "content": "Return plain text."},
        {"role": "user", "content": "Anything, including unstructured text."},
    ]
    raw = 'No storyboard schema. {"arbitrary": true}\n原样返回'
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(
            c.post,
            "/v1/chat/completions",
            headers=auth,
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.4,
                "max_completion_tokens": 50,
                "parameters": {"top_k": 12},
                "stream": stream,
            },
        )
        attempt = poll(c, nh)
        assert attempt["input"]["messages"] == messages
        assert attempt["input"]["parameters"] == {
            "temperature": 0.4,
            "max_tokens": 50,
            "top_k": 12,
        }
        assert attempt["template"]["worker"] == "ollama:" + model
        complete(c, nh, attempt, {"content": raw})
        r = pending.result(timeout=10)
    assert r.status_code == 200, r.text
    assert r.headers["X-PCC-Job-ID"] == attempt["job_id"]
    if stream:
        import json

        chunks = [
            json.loads(s[6:]) for s in r.text.splitlines() if s.startswith("data: {")
        ]
        assert (
            "".join(x["choices"][0]["delta"].get("content", "") for x in chunks) == raw
        )
        assert "data: [DONE]" in r.text
    else:
        assert r.json()["choices"][0]["message"]["content"] == raw


@pytest.mark.parametrize(
    "extra",
    [
        {"temperature": True},
        {"max_tokens": True},
        {"seed": -1},
        {"n": 2},
        {"temperature": 0.3, "parameters": {"temperature": 0.4}},
        {"max_tokens": 10, "max_completion_tokens": 11},
        {"parameters": {"context_length": 500}},
        {"max_tokens": 101},
        {"response_format": {"type": "json_object"}},
        {"tools": []},
    ],
)
def test_native_text_rejects_conflicts_and_admin_override(env, extra):
    _, c, auth, _ = env
    model, _ = setup_model(env, parameters={"max_tokens": 100})
    r = c.post(
        "/v1/chat/completions",
        headers=auth,
        json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            **extra,
        },
    )
    assert r.status_code == 422, r.text
    with env[0].state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.parametrize("fmt", ["url", "b64_json"])
def test_native_image_selected_model_seed_negative_prompt_and_idempotency(env, fmt):
    _, c, auth, _ = env
    model, nh = setup_model(env, "image")
    body = dict(
        model=model,
        prompt="A rabbit",
        negative_prompt="watermark",
        seed=12345,
        width=512,
        height=512,
        n=1,
        response_format=fmt,
    )
    headers = {**auth, "Idempotency-Key": "native-image-" + fmt}
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(
            c.post, "/v1/images/generations", headers=headers, json=body
        )
        attempt = poll(c, nh)
        assert attempt["input"] == dict(
            prompt="A rabbit", size="512x512", seed=12345, negative_prompt="watermark"
        )
        assert attempt["template"]["worker"] == "comfyui:" + model
        complete(
            c, nh, attempt, {"seed": 12345, "negative_prompt": "watermark"}, image=True
        )
        r = pending.result(timeout=10)
    assert r.status_code == 200, r.text
    assert r.headers["X-PCC-Job-ID"] == attempt["job_id"] == r.json()["job_id"]
    item = r.json()["data"][0]
    assert item["seed"] == 12345 and item["negative_prompt"] == "watermark"
    assert item["sha256"] == hashlib.sha256(PNG).hexdigest()
    if fmt == "b64_json":
        assert base64.b64decode(item["b64_json"]) == PNG
        assert "url" not in item
    else:
        assert c.get(item["url"]).status_code == 401
        assert c.get(item["url"], headers=auth).content == PNG
        assert "/api/v1/artifacts/" in item["url"]
    assert (
        c.post("/v1/images/generations", headers=headers, json=body).json() == r.json()
    )
    assert (
        c.post(
            "/v1/images/generations",
            headers=headers,
            json={**body, "prompt": "Changed"},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"n": 2},
        {"width": 512},
        {"width": 512, "height": 512, "size": "1024x1024"},
        {"width": True, "height": 512},
        {"seed": True},
        {"response_format": "path"},
    ],
)
def test_native_image_invalid_inputs(env, extra):
    _, c, auth, _ = env
    model, _ = setup_model(env, "image")
    assert (
        c.post(
            "/v1/images/generations",
            headers=auth,
            json={"model": model, "prompt": "rabbit", **extra},
        ).status_code
        == 422
    )


def test_native_timeout_preserves_job_and_retry_key(env):
    app, c, auth, _ = env
    model, _ = setup_model(env, "image")
    app.state.settings.image_wait_seconds = 1
    headers = {**auth, "Idempotency-Key": "native-timeout"}
    body = {"model": model, "prompt": "rabbit", "seed": None}
    first = c.post("/v1/images/generations", headers=headers, json=body)
    assert first.status_code == 504, first.text
    jid = first.headers["X-PCC-Job-ID"]
    assert c.get("/api/v1/jobs/" + jid, headers=auth).json()["status"] == "queued"
    retry = c.post("/v1/images/generations", headers=headers, json=body)
    assert retry.status_code == 504 and retry.headers["X-PCC-Job-ID"] == jid
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_native_scope_and_ambiguous_model(env):
    app, c, auth, uid = env
    model, _ = setup_model(env, "image")
    with app.state.sessions() as db:
        db.add(
            APIKey(
                user_id=uid,
                hash=digest("write-only", app.state.settings),
                scopes=["jobs:write"],
                expires_at=time.time() + 300,
            )
        )
        db.commit()
    body = {"model": model, "prompt": "rabbit"}
    assert (
        c.post(
            "/v1/images/generations",
            headers={"Authorization": "Bearer write-only"},
            json=body,
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/v1/admin/templates",
            headers=auth,
            json={
                "id": "duplicate",
                "name": "duplicate",
                "kind": "ai.image",
                "worker": "comfyui:" + model,
                "model": model,
            },
        ).status_code
        == 409
    )
    # Simulate legacy inconsistent data, bypassing normal registration safeguards.
    with app.state.sessions() as db:
        original = db.get(TaskTemplate, model)
        db.add(
            TaskTemplate(
                id="legacy-duplicate",
                name="legacy",
                kind=original.kind,
                worker=original.worker,
                config=original.config,
            )
        )
        db.commit()
    assert c.post("/v1/images/generations", headers=auth, json=body).status_code == 409
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.parametrize("bad", ["missing", "expired", "corrupt", "not-png", "failed"])
def test_native_image_rejects_failed_or_invalid_result(env, bad):
    app, c, auth, _ = env
    model, nh = setup_model(env, "image")
    headers = {**auth, "Idempotency-Key": "invalid-result"}
    body = {"model": model, "prompt": "rabbit"}
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(
            c.post, "/v1/images/generations", headers=headers, json=body
        )
        attempt = poll(c, nh)
        # Manipulate completed data before the waiter observes it, within one transaction.
        with app.state.sessions() as db:
            job = db.get(Job, attempt["job_id"])
            job.status = "failed" if bad == "failed" else "completed"
            job.result = {}
            if bad not in {"missing", "failed"}:
                key = uuid.uuid4().hex
                raw = b"not a PNG" if bad == "not-png" else PNG
                size, checksum = app.state.storage.put(key, raw)
                db.add(
                    Artifact(
                        job_id=job.id,
                        attempt_id=job.current_attempt_id,
                        owner_id=job.owner_id,
                        name="image.png",
                        key=key,
                        size=size,
                        sha256="0" * 64 if bad == "corrupt" else checksum,
                        expires_at=time.time() + (-1 if bad == "expired" else 300),
                    )
                )
            db.commit()
        r = pending.result(timeout=10)
    assert r.status_code == (
        503 if bad == "failed" else 409 if bad == "corrupt" else 502
    ), r.text
    assert r.headers["X-PCC-Job-ID"] == attempt["job_id"]
    assert "data" not in r.json()


def test_existing_async_image_defaults_and_new_dimensions(env):
    _, c, auth, _ = env
    model, nh = setup_model(
        env, "image", image_parameters={"seed": 77, "default_size": "1024x1024"}
    )
    r = c.post(
        "/api/v1/images/generations",
        headers=auth,
        json={
            "model": model,
            "prompt": "rabbit",
            "seed": None,
            "negative_prompt": None,
        },
    )
    assert r.status_code == 202, r.text
    attempt = poll(c, nh)
    assert attempt["input"] == {"prompt": "rabbit", "size": "1024x1024"}
    assert attempt["template"]["config"]["image_parameters"]["seed"] == 77
    complete(c, nh, attempt, {}, image=True)
    r = c.post(
        "/api/v1/images/generations",
        headers=auth,
        json={
            "model": model,
            "prompt": "rabbit",
            "width": 512,
            "height": 512,
        },
    )
    assert r.status_code == 202, r.text
    assert poll(c, nh)["input"]["size"] == "512x512"


def test_native_multimodal_messages_are_not_rewritten(env):
    _, c, auth, _ = env
    model, nh = setup_model(env, parameters={"vision": True})
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image."},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + base64.b64encode(PNG).decode()
                    },
                },
            ],
        }
    ]
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(
            c.post,
            "/v1/chat/completions",
            headers=auth,
            json={"model": model, "messages": messages},
        )
        attempt = poll(c, nh)
        assert attempt["input"]["messages"] == messages
        complete(c, nh, attempt, {"content": "Caller controls the task."})
        r = pending.result(timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "Caller controls the task."
