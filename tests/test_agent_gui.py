"""Real loopback GUI, config integrity and CSRF checks. Author: FubukiHanasaki."""

import json
import stat
import tomllib

import httpx
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.gui import ConfigStore, GUIError, gui_server, toml_config


def test_config_roundtrip_and_conflict_preserve_all_settings(tmp_path):
    store = ConfigStore(tmp_path / "agent.toml")
    state = store.read()
    state["config"].update(
        server="https://example.com",
        name='节点 "A"',
        allowed_users=["user-1"],
        enable_compute=False,
        comfyui_workflows={
            "rabbit.image": {
                "file": "tmp/工作流.json",
                "prompt_node": "6",
                "latent_node": "5",
                "min_vram_mb": 1024,
            }
        },
    )
    saved = store.save({key: state[key] for key in ("config", "revision")})
    actual = AgentConfig.load(store.path)
    assert actual.name == '节点 "A"'
    assert actual.comfyui_workflows["rabbit.image"].file == tmp_path / "tmp/工作流.json"
    assert saved["revision"] != state["revision"]
    with pytest.raises(GUIError, match="其他窗口"):
        store.save({key: state[key] for key in ("config", "revision")})
    assert not list((tmp_path / "tmp/gui-config").iterdir())
    if __import__("os").name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_toml_serializer_preserves_windows_paths_and_omits_null():
    config = AgentConfig(
        server="https://example.com",
        name="node",
        comfyui_python=r"C:\PCC\Python\python.exe",
        ninfer_executable=r"C:\PCC\NInfer\ninfer-serve.exe",
        ninfer_models={"qwen3.8-27b": r"C:\PCC\Models\qwen.ninfer"},
        comfyui_workflows={
            "a.b": {
                "file": r"C:\PCC\工作流.json",
                "prompt_node": "6",
                "latent_node": "5",
            }
        },
    )
    text = toml_config(config.model_dump(mode="json"))
    result = AgentConfig(**tomllib.loads(text))
    assert result == config
    assert "null" not in text


def test_gui_rejects_symlink_config_without_touching_target(tmp_path):
    path = tmp_path / "private.toml"
    path.write_text("unchanged")
    link = tmp_path / "agent.toml"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(GUIError, match="符号链接"):
        ConfigStore(link).read()
    with pytest.raises(GUIError):
        ConfigStore(link).save({"config": {}, "revision": "anything"})
    assert path.read_text() == "unchanged"


def test_gui_real_http_rejects_cross_site_dns_rebinding_and_stale_writes(tmp_path):
    with gui_server(tmp_path / "agent.toml") as server:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            assert client.get("/api/state").status_code == 403
            page = client.get("/")
            assert page.status_code == 200 and "set-cookie" not in page.headers
            assert client.get("/api/state").status_code == 403
            assert (
                client.post(
                    "/api/session",
                    json={"token": "wrong"},
                    headers={"Origin": server.origin},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/api/session",
                    json={"token": server.bootstrap},
                    headers={"Origin": "https://evil.example"},
                ).status_code
                == 403
            )
            session = client.post(
                "/api/session",
                json={"token": server.bootstrap},
                headers={"Origin": server.origin},
            )
            assert (
                session.status_code == 200
                and "HttpOnly" in session.headers["set-cookie"]
            )
            assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
            # A cookie copied from another local TCP port is insufficient.
            assert client.get("/api/state").status_code == 403
            client.headers["X-PCC-GUI"] = session.json()["key"]
            state = client.get("/api/state").json()
            payload = {key: state[key] for key in ("config", "revision")}
            assert client.post("/api/config", json=payload).status_code == 403
            auth = {"Origin": server.origin, "X-CSRF-Token": state["csrf"]}
            assert (
                client.post(
                    "/api/config",
                    json=payload,
                    headers={**auth, "Origin": "https://evil.example"},
                ).status_code
                == 403
            )
            assert (
                client.get("/api/state", headers={"Host": "evil.example"}).status_code
                == 403
            )
            assert (
                client.get(
                    "/api/state", headers={"Sec-Fetch-Site": "cross-site"}
                ).status_code
                == 403
            )
            assert (
                client.get(
                    "/api/state", headers={"Origin": "https://evil.example"}
                ).status_code
                == 403
            )
            assert client.get("/../config.py").status_code == 404
            payload["config"]["server"] = "https://example.com"
            assert (
                client.post("/api/config", json=payload, headers=auth).status_code
                == 200
            )
            assert (
                client.post("/api/config", json=payload, headers=auth).status_code
                == 409
            )
            invalid = {
                "config": {**payload["config"], "server": "http://public.example"},
                "revision": client.get("/api/state").json()["revision"],
            }
            assert (
                client.post("/api/config", json=invalid, headers=auth).status_code
                == 422
            )
            assert AgentConfig.load(server.store.path).server == "https://example.com"
            assert (
                client.post(
                    "/api/config",
                    content=b"x",
                    headers={**auth, "Content-Type": "text/plain"},
                ).status_code
                == 415
            )
            assert (
                client.post(
                    "/api/config",
                    content=b"x" * (128 * 1024 + 1),
                    headers={**auth, "Content-Type": "application/json"},
                ).status_code
                == 413
            )
            assert client.get("/api/metrics").json()["memory_total_mb"] > 0
            for name in ("gui.js", "gui.css", "theme.css"):
                assert client.get("/" + name).status_code == 200


def test_gui_enrollment_uses_saved_tls_config_and_never_returns_secret(
    tmp_path, monkeypatch
):
    import pcc_agent.gui as module

    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, path, json):
            calls.append((path, json))
            return httpx.Response(
                200,
                json={"node_id": "node-1", "credential": "secret-test-only"},
                request=httpx.Request("POST", "https://example.com"),
            )

    monkeypatch.setattr(module, "transport", lambda config: Client())
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    store = ConfigStore(tmp_path / "agent.toml")
    state = store.read()
    store.save({key: state[key] for key in ("config", "revision")})
    response = module.register(store, {"token": "one-time-test"})
    assert "secret-test-only" not in json.dumps(response)
    assert calls[0][1]["token"] == "one-time-test"
    assert (
        json.loads((tmp_path / "tmp/agent-credential.json").read_text())["credential"]
        == "secret-test-only"
    )
    with pytest.raises(GUIError, match="已有凭证"):
        module.register(store, {"token": "new-token"})
    assert len(calls) == 1
