"""Per-request image seed does not change administrator-owned settings. FubukiHanasaki."""

import json

import pytest
from fastapi import HTTPException

from pcc_agent.comfyui import approved_workflow
from pcc_agent.inference import image_seed
from pcc_server.templates import validate_input
from test_comfyui import config


@pytest.mark.parametrize("seed", [0, 12345, 2**53 - 1, None])
def test_api_seed_reaches_task_input(env, seed):
    _, client, auth, _ = env
    response = client.post(
        "/api/v1/admin/templates",
        headers=auth,
        json={
            "id": "seed-image",
            "name": "Seed image",
            "kind": "ai.image",
            "worker": "comfyui:seed-image",
            "model": "seed-image",
            "image_parameters": {"seed": 42},
        },
    )
    assert response.status_code == 201, response.text
    result = client.post(
        "/api/v1/images/generations",
        headers=auth,
        json={"model": "seed-image", "prompt": "A rabbit", "seed": seed},
    )
    assert result.status_code == 202, result.text
    from pcc_server.models import Job

    app = env[0]
    with app.state.sessions() as db:
        row = db.get(Job, result.json()["job_id"])
        assert row.input.get("seed") == seed
        assert row.requirements["memory_mb"] == 256


@pytest.mark.parametrize("seed", [-1, 2**53, True, "123", 1.5])
def test_api_rejects_invalid_seed(env, seed):
    _, client, auth, _ = env
    assert (
        client.post(
            "/api/v1/images/generations",
            headers=auth,
            json={"model": "unused", "prompt": "rabbit", "seed": seed},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "requested,expected", [(12345, 12345), (0, 0), (None, 42), (2**53 - 1, 2**53 - 1)]
)
def test_comfy_seed_binding_and_defaults(tmp_path, requested, expected):
    cfg = config(tmp_path)
    approved = cfg.comfyui_workflows["local-image"]
    graph = json.loads(approved.file.read_text())
    graph["sampler"] = {
        "class_type": "KSampler",
        "inputs": {"steps": 99, "cfg": 1, "seed": 555},
    }
    approved.file.write_text(json.dumps(graph))
    approved.sampler_node = "sampler"
    result = approved_workflow(
        cfg,
        "local-image",
        {"prompt": "rabbit", "size": "512x512", "seed": requested},
        {"seed": 42, "steps": 4, "cfg": 2},
    )
    assert result["sampler"]["inputs"] == {"seed": expected, "steps": 4, "cfg": 2}
    assert json.loads(approved.file.read_text())["sampler"]["inputs"]["seed"] == 555
    absent = approved_workflow(
        cfg, "local-image", {"prompt": "rabbit", "size": "512x512"}, {"seed": 42}
    )
    assert absent["sampler"]["inputs"]["seed"] == 42


def test_unbound_seed_is_not_silently_ignored(tmp_path):
    cfg = config(tmp_path)
    with pytest.raises(RuntimeError, match="KSampler"):
        approved_workflow(
            cfg, "local-image", {"prompt": "rabbit", "size": "512x512", "seed": 123}
        )


@pytest.mark.parametrize("bad", [-1, True, 1.0, "42", 2**53])
def test_direct_job_and_agent_validation(bad):
    from types import SimpleNamespace

    template = SimpleNamespace(kind="ai.image", worker="comfyui:local")
    with pytest.raises(HTTPException):
        validate_input(template, {"prompt": "rabbit", "size": "512x512", "seed": bad})
    with pytest.raises(ValueError):
        image_seed(bad, 42)
