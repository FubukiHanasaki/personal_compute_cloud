from managed_fixtures import configure_managed_memory

"""Inference protocol and model update safety. Author: FubukiHanasaki."""

import base64
import copy
import json
from pathlib import Path
import httpx
import pytest
from pcc_agent.config import AgentConfig, ComfyWorkflow
from pcc_agent.inference import parameters, validate_messages, image_bytes
from pcc_agent.ninfer import NInferBackend
from pcc_agent.services import OllamaBackend, ServiceManager
from pcc_agent.comfyui import approved_workflow
from test_services import FakeBackend

PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (1).to_bytes(4, "big") * 2
URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
RES = {"cpu": 1, "memory_mb": 512, "vram_mb": 1024}


def test_ollama_translates_vision_and_admin_defaults(tmp_path):
    cfg = AgentConfig(server="https://example.com", name="test", work_root=tmp_path)
    backend = OllamaBackend(cfg, "qwen3-vl:8b", RES)
    backend.parameters = parameters(
        {"vision": True, "context_length": 16384, "max_tokens": 4096}
    )
    calls = []

    def transport(request):
        if request.url.path == "/api/chat":
            assert request.extensions["timeout"]["read"] > 120
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": "一只兔子"}})
        return httpx.Response(200, json={"models": []})

    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://127.0.0.1:11435", transport=httpx.MockTransport(transport)
    )
    backend._require_listener = lambda port: None
    result = backend.chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这是什么？"},
                    {"type": "image_url", "image_url": {"url": URL}},
                ],
            }
        ],
        overrides={"temperature": 0.2},
        timeout_seconds=240,
    )
    assert result["result"]["content"] == "一只兔子"
    assert calls[0]["messages"][0]["images"] == [URL.split(",")[1]]
    assert (
        calls[0]["options"]["num_ctx"] == 16384
        and calls[0]["options"]["num_predict"] == 4096
    )
    assert calls[0]["options"]["temperature"] == 0.2
    backend.client.close()


def test_ninfer_command_and_openai_multimodal_protocol(tmp_path):
    cfg = AgentConfig(
        managed_commit_budget_mb=32768,
        managed_available_memory_mb={"ninfer:qwen3.8-27b": 1024},
        server="https://example.com",
        name="test",
        work_root=tmp_path,
        ninfer_executable=str(tmp_path / "ninfer-serve"),
        ninfer_models={"qwen3.8-27b": tmp_path / "qwen.ninfer"},
    )
    backend = NInferBackend(cfg, "qwen3.8-27b", RES)
    backend.parameters = parameters(
        {"kv_cache_type": "fp8", "kv_capacity_mb": 2048, "vision": True}, "ninfer"
    )
    args = backend.command()
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert "--vision" in args
    assert args[args.index("--kv-dtype") + 1] == "fp8"
    assert args[args.index("--context-capacity") + 1] == "2048MiB"
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://127.0.0.1:8089", transport=httpx.MockTransport(transport)
    )
    backend._require_listener = lambda port: None
    content = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": URL}}]}
    ]
    assert backend.chat(content)["result"]["content"] == "ok"
    assert calls[0]["messages"] == content
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    backend.client.close()


def test_changed_settings_stop_old_backend_before_reloading(tmp_path):
    cfg = AgentConfig(
        server="https://example.com",
        name="test",
        work_root=tmp_path,
        allocatable_memory_mb=1024,
        allocatable_vram_mb=1024,
    )
    manager = ServiceManager(cfg, factory=FakeBackend)
    configure_managed_memory(manager, ["ollama:m"])
    command = {
        "definition_id": "m",
        "config": {"worker": "ollama:m", "model": "m", "resources": RES},
        "idle_seconds": 300,
        "desired_state": "auto",
    }
    manager.sync([command])
    service = manager.acquire("m")
    manager.ready(service)
    old = service.backend
    changed = copy.deepcopy(command)
    changed["config"]["parameters"] = {"context_length": 16384}
    manager.sync([changed])
    assert not old.stopped
    manager.release(service)
    manager.sync([changed])
    assert old.stopped
    manager.ready(manager.acquire("m"))
    assert service.backend.parameters["context_length"] == 16384
    manager.release(service)
    manager.stop_all()


def test_qwen21_workflow_binds_only_approved_nodes(tmp_path):
    source = Path("deploy/workflows/qwen-image-2.1-api.example.json")
    cfg = AgentConfig(
        managed_commit_budget_mb=32768,
        managed_available_memory_mb={"comfyui:qwen": 1024},
        server="https://example.com",
        name="test",
        work_root=tmp_path,
        comfyui_workflows={
            "qwen": ComfyWorkflow(
                file=source,
                prompt_node="text",
                latent_node="latent",
                sampler_node="sampler",
                image_nodes=["ref1", "ref2", "ref3", "ref4"],
            )
        },
    )
    graph = approved_workflow(
        cfg,
        "qwen",
        {"prompt": "兔子击败恐龙", "size": "1536x1024", "images": [URL]},
        {"steps": 30, "cfg": 1.0, "seed": 123, "default_size": "1024x1024"},
    )
    assert graph["text"]["inputs"]["prompt"] == "兔子击败恐龙"
    assert graph["latent"]["inputs"]["width"] == 1536
    assert graph["sampler"]["inputs"]["steps"] == 30
    assert graph["sampler"]["inputs"]["seed"] == 123
    assert graph["ref1"]["inputs"]["image"] == "reference-0.png"
    assert "ref2" not in graph
    assert "images.image_2" not in graph["text"]["inputs"]
    with pytest.raises(ValueError):
        approved_workflow(
            cfg,
            "qwen",
            {"prompt": "x", "size": "1024x1024", "images": ["file:///etc/passwd"]},
        )


def test_media_and_parameter_limits():
    assert image_bytes(URL) == PNG
    large = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (100000).to_bytes(4, "big") * 2
    with pytest.raises(ValueError):
        image_bytes("data:image/png;base64," + base64.b64encode(large).decode())
    with pytest.raises(ValueError):
        parameters({"kv_cache_type": "fp8"}, "ollama")
    with pytest.raises(ValueError):
        parameters({"kv_cache_type": "fp8", "top_k": 30}, "ninfer")
    with pytest.raises(ValueError):
        parameters({"max_tokens": 10000, "context_length": 8192})
    with pytest.raises(ValueError):
        validate_messages(
            [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": URL}}],
                }
            ]
        )


def test_reference_images_cannot_share_a_workflow_binding(tmp_path):
    with pytest.raises(ValueError, match="不同"):
        ComfyWorkflow(
            file=tmp_path / "approved.json",
            prompt_node="text",
            latent_node="latent",
            image_nodes=["ref1", "ref1"],
        )


def test_non_thinking_ollama_model_omits_unsupported_switch(tmp_path):
    cfg = AgentConfig(server="https://example.com", name="test", work_root=tmp_path)
    backend = OllamaBackend(cfg, "qwen3-vl:8b-instruct", RES)
    backend.supports_thinking = False
    calls = []

    def transport(request):
        if request.url.path == "/api/chat":
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": "ok"}})
        return httpx.Response(200, json={"models": []})

    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://127.0.0.1:11435", transport=httpx.MockTransport(transport)
    )
    backend._require_listener = lambda port: None
    assert (
        backend.chat([{"role": "user", "content": "hello"}])["result"]["content"]
        == "ok"
    )
    assert "think" not in calls[0]
    with pytest.raises(RuntimeError, match="thinking"):
        backend.chat(
            [{"role": "user", "content": "hello"}], overrides={"thinking": True}
        )
    backend.client.close()
