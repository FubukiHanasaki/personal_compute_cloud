"""Original adapter contract against a deterministic PCC transport. FubukiHanasaki."""

import hashlib
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from pcc_server.text2visual import Adapter, Config, create_gateway


def plan_request():
    return {
        "request_id": "r-123",
        "project_id": "demo",
        "source": {
            "source_id": "doc-1",
            "title": "Water",
            "type": "paste",
            "language": "en",
            "blocks": [
                {
                    "block_id": "b1",
                    "text": "Water evaporates.",
                    "page": None,
                    "char_start": 0,
                    "char_end": 17,
                }
            ],
        },
        "options": {
            "min_scenes": 4,
            "max_scenes": 8,
            "style": {
                "style_id": "flat",
                "description": "flat colours",
                "reference_image": None,
            },
            "seed": None,
        },
    }


def storyboard():
    request = plan_request()
    return {
        "schema_version": "1.0",
        "project_id": "demo",
        "source": {k: v for k, v in request["source"].items() if k != "blocks"},
        "style": request["options"]["style"],
        "key_points": [
            {
                "key_point_id": "k1",
                "text": "Water evaporates",
                "source_ref": {"char_start": 0, "char_end": 17},
            }
        ],
        "scenes": [
            {
                "scene_id": f"s{i}",
                "order": i,
                "title": "Water",
                "caption": "Evaporation",
                "visual_description": "Water",
                "image_prompt": "Water",
                "key_point_ids": ["k1"],
                "source_ref": {"char_start": 0, "char_end": 17},
                "status": "planned",
                "revision": 1,
                "image": None,
            }
            for i in range(1, 5)
        ],
    }


def image_request():
    return {
        "request_id": "r-456",
        "project_id": "demo",
        "scene_id": "s2",
        "scene_revision": 3,
        "prompt": "Water",
        "negative_prompt": "",
        "style_reference_image": None,
        "seed": None,
        "width": 1024,
        "height": 1024,
        "options": {},
    }


@pytest.fixture
def gateway(tmp_path):
    state = {
        "board": storyboard(),
        "job_status": "completed",
        "calls": [],
        "png": b"\x89PNG\r\n\x1a\ncontract-fixture",
        "corrupt": False,
        "status": 200,
    }

    def handle(request):
        state["calls"].append(request)
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": "fixture"})
        path = request.url.path
        if path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "text", "task_type": "ai.chat", "online": True},
                        {
                            "id": "image",
                            "task_type": "ai.image",
                            "online": True,
                            "image_parameters": {"seed": 12345},
                        },
                    ]
                },
            )
        if path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(state["board"])}}]
                },
            )
        if path == "/api/v1/images/generations":
            return httpx.Response(202, json={"job_id": "abc123", "status": "queued"})
        if path == "/api/v1/jobs/abc123":
            return httpx.Response(200, json={"status": state["job_status"]})
        if path.endswith("/artifacts"):
            data = state["png"]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "def456",
                            "name": "image.png",
                            "size": len(data),
                            "sha256": "bad"
                            if state["corrupt"]
                            else hashlib.sha256(data).hexdigest(),
                            "download_url": "https://attacker.invalid/steal",
                        }
                    ]
                },
            )
        if path == "/api/v1/artifacts/def456":
            return httpx.Response(200, content=state["png"])
        raise AssertionError(path)

    config = Config(
        text_model="text",
        image_model="image",
        text_info={"name": "Qwen text"},
        image_info={"name": "Qwen-Image", "version": "2.1"},
        output_dir=str(tmp_path / "output"),
    )
    with httpx.Client(
        base_url="http://pcc", transport=httpx.MockTransport(handle)
    ) as client:
        yield Adapter(config, "test-only-key", client), state


def test_original_plan_contract(gateway):
    adapter, state = gateway
    result = adapter.plan_storyboard(plan_request())
    assert result["status"] == "ok"
    assert result["request_id"] == "r-123"
    assert result["storyboard"] == storyboard()
    assert result["warnings"] == []
    assert result["meta"]["mock"] is False
    assert result["meta"]["resources"]["vram_peak_mb"] is None


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "revision", "image", "reference", "range", "schema", "project"],
)
def test_rejects_invalid_model_output(gateway, mutation):
    adapter, state = gateway
    board = state["board"]
    if mutation == "duplicate":
        board["scenes"][1]["scene_id"] = "s1"
    if mutation == "revision":
        board["scenes"][0].pop("revision")
    if mutation == "image":
        board["scenes"][0]["image"] = {}
    if mutation == "reference":
        board["scenes"][0]["key_point_ids"] = ["k99"]
    if mutation == "range":
        board["scenes"][0]["source_ref"] = {"char_start": 99, "char_end": 0}
    if mutation == "schema":
        board["extra"] = True
    if mutation == "project":
        board["project_id"] = "wrong"
    assert (
        adapter.plan_storyboard(plan_request())["error"]["code"]
        == "SCHEMA_VALIDATION_FAILED"
    )


def test_image_download_and_echo(gateway):
    adapter, state = gateway
    result = adapter.generate_image(image_request())
    assert result["status"] == "ok"
    assert result["scene_id"] == "s2" and result["scene_revision"] == 3
    from pathlib import Path

    assert Path(result["image"]["path"]).read_bytes() == state["png"]
    assert result["image"]["seed"] == 12345
    assert result["image"]["latency_ms"] > 0
    assert all(r.url.host == "pcc" for r in state["calls"])
    submit = next(r for r in state["calls"] if r.method == "POST")
    key = submit.headers["Idempotency-Key"]
    adapter.generate_image(image_request())
    assert [
        r.headers["Idempotency-Key"] for r in state["calls"] if r.method == "POST"
    ] == [key, key]


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("negative_prompt", "text", "UNSUPPORTED_PARAMETER"),
        ("seed", 999, "UNSUPPORTED_PARAMETER"),
        ("width", 100, "INVALID_INPUT"),
        ("scene_revision", True, "INVALID_INPUT"),
        ("options", {"steps": 1}, "UNSUPPORTED_PARAMETER"),
        ("style_reference_image", "/etc/passwd", "INVALID_INPUT"),
    ],
)
def test_image_validation(gateway, field, value, code):
    adapter, state = gateway
    body = image_request()
    body[field] = value
    result = adapter.generate_image(body)
    assert result["status"] == "error" and result["error"]["code"] == code
    assert result["request_id"] == body["request_id"]
    assert not any(r.method == "POST" for r in state["calls"])


def test_failed_and_corrupt_artifacts(gateway):
    adapter, state = gateway
    state["job_status"] = "failed"
    assert (
        adapter.generate_image(image_request())["error"]["code"] == "GENERATION_FAILED"
    )
    state["job_status"] = "completed"
    state["corrupt"] = True
    assert (
        adapter.generate_image(image_request())["error"]["code"] == "GENERATION_FAILED"
    )
    assert not list(__import__("pathlib").Path(adapter.config.output_dir).glob("*.png"))


def test_timeout_does_not_claim_failure_or_retry(gateway, monkeypatch):
    adapter, state = gateway
    state["job_status"] = "queued"
    ticks = iter(range(10000))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    adapter.config.wait_seconds = 30
    result = adapter.generate_image(image_request())
    assert result["error"]["code"] == "TIMEOUT"
    assert result["error"]["retryable"] is False


def test_upstream_error_and_describe(gateway):
    adapter, state = gateway
    assert adapter.describe("image")["ready"] is True
    assert adapter.describe("image")["capabilities"]["per_request_seed"] is False
    state["status"] = 403
    result = adapter.plan_storyboard(plan_request())
    assert result["status"] == "error"
    assert result["error"]["details"]["http_status"] == 403
    assert adapter.describe("text")["ready"] is False


def test_http_contract_auth_malformed_and_methods(gateway):
    adapter, _ = gateway
    token = "local-test-token-" * 3
    with TestClient(create_gateway(adapter, token)) as client:
        headers = {"Authorization": "Bearer " + token}
        assert (
            client.post(
                "/v1/storyboards:plan", headers=headers, json=plan_request()
            ).json()["status"]
            == "ok"
        )
        assert (
            client.post(
                "/v1/images:generate", headers=headers, json=image_request()
            ).json()["status"]
            == "ok"
        )
        response = client.post("/v1/images:generate", headers=headers, content="{")
        assert (
            response.status_code == 200
            and response.json()["error"]["code"] == "INVALID_INPUT"
        )
        assert client.get("/v1/adapters/text", headers=headers).json()["kind"] == "text"
        assert (
            client.get("/v1/adapters/image", headers=headers).json()["kind"] == "image"
        )
        assert (
            client.post("/v1/images:generate", json=image_request()).json()["status"]
            == "error"
        )


def test_configuration_rejects_remote_http_and_weak_auth(gateway):
    adapter, _ = gateway
    config = adapter.config.model_copy(update={"upstream": "http://example.com"})
    with pytest.raises(ValueError):
        Adapter(config, "key")
    with pytest.raises(ValueError):
        create_gateway(adapter, "")
