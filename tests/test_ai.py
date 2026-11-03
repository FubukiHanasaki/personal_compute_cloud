from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.execution import execute
from pcc_agent.platform import isolation_capabilities
from pcc_agent.services import ServiceManager
from conftest import register_node
from test_jobs import claim


def test_model_and_template_pagination_applies_after_authorization(env):
    """Selectors must reach later pages without leaking private models. Fubuki."""
    from fastapi.testclient import TestClient
    from test_auth import login

    app, client, auth, _ = env
    for i in range(4):
        assert (
            client.post(
                "/api/v1/admin/templates",
                headers=auth,
                json={
                    "id": f"model-{i}",
                    "name": f"模型{i}",
                    "kind": "ai.chat",
                    "worker": "synthetic.chat",
                    "model": f"model-{i}",
                    "public": i % 2 == 1,
                },
            ).status_code
            == 201
        )
    client.post(
        "/api/v1/admin/users",
        headers=auth,
        json={
            "username": "model-user",
            "password": "model-user-password-123",
        },
    )
    with TestClient(app) as user:
        login(user, "model-user", "model-user-password-123")
        for path, field in (("/api/v1/templates", "items"), ("/api/v1/models", "data")):
            page = user.get(path + "?limit=1&offset=1").json()
            assert [row["id"] for row in page[field]] == ["model-3"]
            assert page["limit"] == 1 and page["offset"] == 1
            assert user.get(path + "?limit=101").status_code == 422
            assert user.get(path + "?offset=-1").status_code == 422


def test_synthetic_chat_sse_and_async_image_protocol(env, tmp_path):
    if not isolation_capabilities():
        pytest.skip("Requires Linux nonprivileged bubblewrap")
    app, c, auth, _ = env
    for kind in ("chat", "image"):
        assert (
            c.post(
                "/api/v1/admin/templates",
                headers=auth,
                json={
                    "id": kind,
                    "name": kind,
                    "kind": "ai." + kind,
                    "worker": "synthetic." + kind,
                    "model": "test-" + kind,
                    "public": True,
                },
            ).status_code
            == 201
        )
    identity, nh, caps = register_node(
        c, auth, workers=["synthetic.chat", "synthetic.image"]
    )
    response = c.post(
        "/internal/v1/nodes/heartbeat",
        headers=nh,
        json={"capabilities": caps, "policy": {"mode": "shared"}},
    ).json()
    config = AgentConfig(
        enable_compute=True,
        enable_synthetic_ai=True,
        server="https://example.com",
        name="test",
        work_root=tmp_path,
        mode="shared",
    )
    manager = ServiceManager(config)
    manager.sync(response["services"])

    class Transport:
        def post(self, path, json):
            return c.post(path, json=json, headers=nh)

    agent = SimpleNamespace(
        config=config, services=manager, client=Transport(), heartbeat=lambda: None
    )
    with ThreadPoolExecutor(1) as pool:
        for stream in (False, True):
            pending = pool.submit(
                lambda: c.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "model": "test-chat",
                        "messages": [{"role": "user", "content": "hello"}],
                        "stream": stream,
                    },
                )
            )
            until = time.monotonic() + 5
            attempt = None
            while time.monotonic() < until:
                polled = claim(c, nh, __import__("uuid").uuid4().hex)
                if polled.status_code == 200:
                    attempt = polled.json()
                    break
                time.sleep(0.05)
            assert attempt
            execute(agent, attempt)
            result = pending.result(timeout=10)
            assert result.status_code == 200, result.text
            if stream:
                assert "data: [DONE]" in result.text
                assert "chat.completion.chunk" in result.text
            else:
                assert "hello" in result.json()["choices"][0]["message"]["content"]
    image = c.post(
        "/api/v1/images/generations",
        headers=auth,
        json={"model": "test-image", "prompt": "gradient"},
    )
    assert image.status_code == 202
    execute(agent, claim(c, nh, "i" * 32).json())
    files = c.get(
        f"/api/v1/jobs/{image.json()['job_id']}/artifacts", headers=auth
    ).json()["items"]
    assert c.get(files[0]["download_url"], headers=auth).content.startswith(b"\x89PNG")
