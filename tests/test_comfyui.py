import base64
import json
import httpx
import pytest
from pcc_agent.config import AgentConfig, ComfyWorkflow
from pcc_agent.comfyui import ComfyUIBackend, approved_workflow


def config(tmp_path):
    workflow = tmp_path / "approved.json"
    workflow.write_text(
        json.dumps(
            {
                "text": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": "approved default"},
                },
                "latent": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {"width": 256, "height": 256, "batch_size": 1},
                },
                "save": {
                    "class_type": "SaveImage",
                    "inputs": {"filename_prefix": "test"},
                },
            }
        )
    )
    return AgentConfig(
        server="https://example.com",
        name="test",
        work_root=tmp_path,
        comfyui_workflows={
            "local-image": ComfyWorkflow(
                file=workflow, prompt_node="text", latent_node="latent"
            )
        },
    )


def test_only_approved_workflow_nodes_and_parameter_bindings(tmp_path):
    cfg = config(tmp_path)
    graph = approved_workflow(
        cfg, "local-image", {"prompt": "a sunset", "size": "512x512"}
    )
    assert graph["text"]["inputs"]["text"] == "a sunset"
    assert graph["latent"]["inputs"]["width"] == 512
    assert graph["save"]["inputs"]["filename_prefix"].startswith("pcc-")
    cfg.comfyui_workflows["local-image"].file.write_text(
        json.dumps(
            {"text": {"class_type": "ExecuteShell", "inputs": {"command": "whoami"}}}
        )
    )
    with pytest.raises(RuntimeError, match="unapproved"):
        approved_workflow(cfg, "local-image", {"prompt": "test", "size": "256x256"})


def test_image_adapter_uses_native_queue_and_bounded_png_download(tmp_path):
    cfg = config(tmp_path)
    paths = []
    png = b"\x89PNG\r\n\x1a\n" + b"protocol-test-not-a-real-model"

    def transport(request):
        paths.append(request.url.path)
        if request.url.path == "/prompt":
            data = json.loads(request.content)
            assert data["prompt"]["text"]["inputs"]["text"] == "a sunset"
            return httpx.Response(200, json={"prompt_id": "job-123"})
        if request.url.path == "/history/job-123":
            return httpx.Response(
                200,
                json={
                    "job-123": {
                        "outputs": {
                            "save": {
                                "images": [
                                    {
                                        "filename": "output.png",
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
        if request.url.path == "/view":
            return httpx.Response(200, content=png)
        if request.url.path == "/system_stats":
            return httpx.Response(
                200, json={"devices": [{"type": "cuda", "torch_vram_total": 1000}]}
            )
        raise AssertionError(request.url.path)

    backend = ComfyUIBackend(
        cfg, "local-image", {"cpu": 1, "memory_mb": 512, "vram_mb": 1024}
    )
    backend.client.close()
    backend.client = httpx.Client(
        base_url="http://127.0.0.1:8189", transport=httpx.MockTransport(transport)
    )
    try:
        result = backend.generate({"prompt": "a sunset", "size": "256x256"}, 1024)
        assert not result["result"]["synthetic"]
        assert base64.b64decode(result["artifacts"][0]["data_base64"]) == png
        assert paths == ["/prompt", "/history/job-123", "/view", "/system_stats"]
        with pytest.raises(RuntimeError, match="limit"):
            backend.generate({"prompt": "a sunset", "size": "256x256"}, 8)
    finally:
        backend.stop()
        backend.client.close()


def test_server_accepts_explicit_image_backend_and_rejects_model_mismatch(env):
    _, client, auth, _ = env
    body = {
        "id": "real-image",
        "name": "本地图片模型",
        "kind": "ai.image",
        "worker": "comfyui:local-image",
        "model": "local-image",
        "public": True,
    }
    assert (
        client.post("/api/v1/admin/templates", headers=auth, json=body).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/admin/templates",
            headers=auth,
            json={**body, "id": "wrong", "model": "other"},
        ).status_code
        == 422
    )
