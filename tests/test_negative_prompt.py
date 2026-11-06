"""Negative conditioning stays within approved workflows. Author: FubukiHanasaki."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pcc_agent.comfyui import approved_workflow
from pcc_agent.config import ComfyWorkflow
from pcc_server.models import Job
from pcc_server.templates import validate_input
from test_comfyui import config


@pytest.mark.parametrize(
    "negative", [None, "", "text, labels, people", "模糊、水印", "x" * 2000]
)
def test_api_transports_negative_prompt(env, negative):
    app, client, auth, _ = env
    response = client.post(
        "/api/v1/admin/templates",
        headers=auth,
        json={
            "id": "negative-image",
            "name": "Image",
            "kind": "ai.image",
            "worker": "comfyui:negative-image",
            "model": "negative-image",
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/api/v1/images/generations",
        headers=auth,
        json={
            "model": "negative-image",
            "prompt": "rabbit",
            "negative_prompt": negative,
            "seed": 123,
        },
    )
    assert response.status_code == 202, response.text
    with app.state.sessions() as db:
        task = db.get(Job, response.json()["job_id"])
        assert task.input.get("negative_prompt") == negative
        assert task.input["seed"] == 123
        assert task.requirements["memory_mb"] == 256


@pytest.mark.parametrize("negative", [True, 10, [], {}, "x" * 2001])
def test_invalid_negative_prompt_rejected_at_both_boundaries(env, negative):
    _, client, auth, _ = env
    response = client.post(
        "/api/v1/images/generations",
        headers=auth,
        json={"model": "unused", "prompt": "rabbit", "negative_prompt": negative},
    )
    assert response.status_code == 422
    with pytest.raises(HTTPException):
        validate_input(
            SimpleNamespace(kind="ai.image", worker="comfyui:local"),
            {"prompt": "rabbit", "size": "512x512", "negative_prompt": negative},
        )


def bound_config(tmp_path):
    cfg = config(tmp_path)
    approved = cfg.comfyui_workflows["local-image"]
    graph = json.loads(approved.file.read_text())
    graph["negative"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "approved default"},
    }
    graph["sampler"] = {
        "class_type": "KSampler",
        "inputs": {"positive": ["text", 0], "negative": ["negative", 0]},
    }
    approved.file.write_text(json.dumps(graph))
    approved.negative_prompt_node = "negative"
    approved.sampler_node = "sampler"
    return cfg


@pytest.mark.parametrize("negative", [None, "", "text, labels", "x" * 2000])
def test_clip_binding_null_empty_and_nonempty(tmp_path, negative):
    cfg = bound_config(tmp_path)
    approved = cfg.comfyui_workflows["local-image"]
    original = approved.file.read_bytes()
    result = approved_workflow(
        cfg,
        "local-image",
        {"prompt": "rabbit", "size": "512x512", "negative_prompt": negative},
    )
    assert result["text"]["inputs"]["text"] == "rabbit"
    assert result["negative"]["inputs"]["text"] == (
        "approved default" if negative is None else negative
    )
    assert approved.file.read_bytes() == original
    omitted = approved_workflow(
        cfg, "local-image", {"prompt": "rabbit", "size": "512x512"}
    )
    assert omitted["negative"]["inputs"]["text"] == "approved default"


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "shared",
        "wrong_type",
        "wrong_edge",
        "wrong_positive",
        "missing_sampler",
    ],
)
def test_invalid_binding_never_silently_applies(tmp_path, fault):
    cfg = bound_config(tmp_path)
    approved = cfg.comfyui_workflows["local-image"]
    graph = json.loads(approved.file.read_text())
    if fault == "missing":
        approved.negative_prompt_node = ""
    if fault == "shared":
        approved.negative_prompt_node = "text"
    if fault == "wrong_type":
        graph["negative"]["class_type"] = "SaveImage"
    if fault == "wrong_edge":
        graph["sampler"]["inputs"]["negative"] = ["text", 0]
    if fault == "wrong_positive":
        graph["sampler"]["inputs"]["positive"] = ["negative", 0]
    if fault == "missing_sampler":
        approved.sampler_node = ""
    approved.file.write_text(json.dumps(graph))
    with pytest.raises(RuntimeError):
        approved_workflow(
            cfg,
            "local-image",
            {"prompt": "rabbit", "size": "512x512", "negative_prompt": "text, labels"},
        )


@pytest.mark.parametrize("explicit_binding", [False, True])
def test_qwen21_combined_encoder(explicit_binding, tmp_path):
    cfg = config(tmp_path)
    cfg.comfyui_workflows["local-image"] = ComfyWorkflow(
        file=Path("deploy/workflows/qwen-image-2.1-api.example.json"),
        prompt_node="text",
        latent_node="latent",
        sampler_node="sampler",
        negative_prompt_node="text" if explicit_binding else "",
        image_nodes=["ref1", "ref2", "ref3", "ref4"],
    )
    graph = approved_workflow(
        cfg,
        "local-image",
        {"prompt": "rabbit", "size": "1024x1024", "negative_prompt": "labels, text"},
    )
    assert graph["text"]["inputs"]["prompt"] == "rabbit"
    assert graph["text"]["inputs"]["negative_prompt"] == "labels, text"
    assert graph["sampler"]["inputs"]["negative"] == ["text", 1]
    assert graph["sampler"]["inputs"]["positive"] == ["text", 0]


def test_synthetic_refuses_nonempty_negative():
    with pytest.raises(HTTPException):
        validate_input(
            SimpleNamespace(kind="ai.image", worker="synthetic.image"),
            {"prompt": "rabbit", "size": "512x512", "negative_prompt": "text"},
        )


def test_actual_comfy_wire_and_completed_result(tmp_path):
    import httpx
    from pcc_agent.comfyui import ComfyUIBackend

    cfg = bound_config(tmp_path)
    seen = []

    def transport(request):
        path = request.url.path
        if path == "/prompt":
            graph = json.loads(request.content)["prompt"]
            seen.append(graph["negative"]["inputs"]["text"])
            assert graph["text"]["inputs"]["text"] == "rabbit"
            return httpx.Response(200, json={"prompt_id": "negative-test"})
        if path == "/history/negative-test":
            return httpx.Response(
                200,
                json={
                    "negative-test": {
                        "outputs": {
                            "save": {
                                "images": [
                                    {
                                        "filename": "out.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                        "status": {"completed": True},
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nwire-fixture")
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": []})
        if path == "/history":
            return httpx.Response(200)
        raise AssertionError(path)

    backend = ComfyUIBackend(
        cfg, "local-image", {"cpu": 1, "memory_mb": 512, "vram_mb": 1024}
    )
    backend.controller = SimpleNamespace(
        listener_ready=lambda port: True, stop=lambda: None
    )
    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://127.0.0.1:8189", transport=httpx.MockTransport(transport)
    )
    try:
        result = backend.generate(
            {
                "prompt": "rabbit",
                "size": "512x512",
                "seed": 987,
                "negative_prompt": "labels, text",
            },
            1024,
        )
        assert seen == ["labels, text"]
        assert result["result"]["negative_prompt"] == "labels, text"
        assert result["result"]["seed"] == 987
    finally:
        backend.stop()
        backend.client.close()
