"""Original adapter contract against a deterministic PCC transport. FubukiHanasaki."""

import hashlib
import json
import time
import struct
import zlib

import httpx
import pytest
from fastapi.testclient import TestClient

from pcc_server.text2visual import Adapter, Config, ModelInfo, create_gateway


def png_fixture():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 1024, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff" * 3072) * 1024))
        + chunk(b"IEND", b"")
    )


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
        "png": png_fixture(),
        "seed": 12345,
        "corrupt": False,
        "status": 200,
    }

    def handle(request):
        state["calls"].append(request)
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": "fixture"})
        path = request.url.path
        if path == "/api/v1/models":
            rows = state.get(
                "models",
                [
                    {"id": "text", "task_type": "ai.chat", "online": True},
                    {
                        "id": "image",
                        "task_type": "ai.image",
                        "online": True,
                        "image_parameters": {"seed": 12345},
                    },
                ],
            )
            offset = int(request.url.params.get("offset", "0"))
            return httpx.Response(200, json={"data": rows[offset : offset + 100]})
        if path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-" + "f" * 32,
                    "choices": [
                        {
                            "message": {
                                "content": state.get(
                                    "content", json.dumps(state["board"])
                                )
                            }
                        }
                    ],
                },
            )
        if path == "/api/v1/images/generations":
            state["negative"] = json.loads(request.content).get("negative_prompt")
            supplied = json.loads(request.content).get("seed")
            state["seed"] = 12345 if supplied is None else supplied
            return httpx.Response(202, json={"job_id": "abc123", "status": "queued"})
        if path == "/api/v1/jobs/abc123":
            return httpx.Response(
                200,
                json={
                    "status": state["job_status"],
                    "result": {
                        "seed": state["seed"],
                        "negative_prompt": state.get(
                            "confirm_negative", state["negative"]
                        ),
                    },
                },
            )
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
        ("negative_prompt", True, "INVALID_INPUT"),
        ("negative_prompt", "x" * 2001, "INVALID_INPUT"),
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
    assert adapter.describe("image")["capabilities"]["per_request_seed"] is True
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
        oversized = client.post(
            "/v1/images:generate",
            headers={**headers, "Content-Length": str(13 * 1024 * 1024)},
            content="{}",
        )
        assert (
            oversized.status_code == 200
            and oversized.json()["error"]["code"] == "INVALID_INPUT"
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


@pytest.mark.parametrize("seed", [None, 0, 999, 2**53 - 1])
def test_image_seed_passthrough(gateway, seed):
    adapter, state = gateway
    request = image_request()
    request["seed"] = seed
    result = adapter.generate_image(request)
    assert result["status"] == "ok", json.dumps(result)
    assert result["image"]["seed"] == (12345 if seed is None else seed)
    submitted = next(r for r in state["calls"] if r.method == "POST")
    assert json.loads(submitted.content)["seed"] == seed


@pytest.mark.parametrize("explicit_selection", [False, True])
def test_gateway_against_actual_pcc_job_and_artifact_routes(
    env, tmp_path, explicit_selection
):
    """Real PCC routes/DB/lease/upload; deterministic worker completion, not model inference."""
    import base64
    import uuid
    from conftest import register_node
    from test_jobs import claim, lease

    _, pcc, auth, _ = env
    response = pcc.post(
        "/api/v1/admin/templates",
        headers=auth,
        json={
            "id": "gateway-image",
            "name": "Image",
            "kind": "ai.image",
            "worker": "comfyui:gateway-image",
            "model": "gateway-image",
            "image_parameters": {"seed": 42},
        },
    )
    assert response.status_code == 201, response.text
    _, node_auth, _ = register_node(pcc, auth, workers=["comfyui:gateway-image"])
    png = png_fixture()

    def forward(request):
        headers = {
            **auth,
            "Content-Type": request.headers.get("Content-Type", "application/json"),
            **(
                {"Idempotency-Key": request.headers["Idempotency-Key"]}
                if "Idempotency-Key" in request.headers
                else {}
            ),
        }
        result = pcc.request(
            request.method,
            str(request.url.copy_with(scheme="http", host="testserver")),
            content=request.content,
            headers=headers,
        )
        assert result.status_code < 400, (
            str(request.url),
            result.text,
            request.content,
        )
        if (
            request.method == "POST"
            and request.url.path.endswith("/generations")
            and result.status_code == 202
        ):
            attempt_response = claim(pcc, node_auth, uuid.uuid4().hex)
            assert attempt_response.status_code == 200, attempt_response.text
            attempt = attempt_response.json()
            assert attempt["input"]["seed"] == 777
            assert attempt["input"]["negative_prompt"] == "text, labels, people"
            jid = attempt["job_id"]
            uploaded = pcc.post(
                f"/internal/v1/jobs/{jid}/artifacts",
                headers=node_auth,
                json={
                    **lease(attempt),
                    "name": "image.png",
                    "data_base64": base64.b64encode(png).decode(),
                    "sha256": hashlib.sha256(png).hexdigest(),
                },
            )
            assert uploaded.status_code == 201, uploaded.text
            completed = pcc.post(
                f"/internal/v1/jobs/{jid}/complete",
                headers=node_auth,
                json={
                    **lease(attempt),
                    "result": {
                        "seed": 777,
                        "synthetic": False,
                        "negative_prompt": "text, labels, people",
                    },
                    "artifact_ids": [uploaded.json()["artifact_id"]],
                },
            )
            assert completed.status_code == 200, completed.text
        return httpx.Response(
            result.status_code,
            content=result.content,
            headers={
                "Content-Type": result.headers.get("Content-Type", "application/json")
            },
        )

    config = Config(
        text_model="unused",
        image_model="unused-default" if explicit_selection else "gateway-image",
        text_info={"name": "Unused"},
        image_info={"name": "Fixture"},
        output_dir=str(tmp_path / "gateway-output"),
    )
    with httpx.Client(
        base_url="http://testserver", transport=httpx.MockTransport(forward)
    ) as upstream:
        result = Adapter(config, "owner-key", upstream).generate_image(
            {
                **image_request(),
                **({"model": "gateway-image"} if explicit_selection else {}),
                "seed": 777,
                "negative_prompt": "text, labels, people",
            }
        )
    assert result["status"] == "ok", json.dumps(result)
    assert result["image"]["seed"] == 777


@pytest.mark.parametrize(
    "negative", ["", "text, labels, people", "模糊、水印", "x" * 2000]
)
def test_negative_prompt_passthrough_and_return(gateway, negative):
    adapter, state = gateway
    result = adapter.generate_image({**image_request(), "negative_prompt": negative})
    assert result["status"] == "ok", result
    assert result["image"]["negative_prompt"] == negative
    posted = next(r for r in state["calls"] if r.method == "POST")
    assert json.loads(posted.content)["negative_prompt"] == negative
    assert adapter.describe("image")["capabilities"]["negative_prompt"] is True


@pytest.mark.parametrize("confirmation", [None, "wrong text"])
def test_backend_must_confirm_negative_prompt(gateway, confirmation):
    adapter, state = gateway
    state["confirm_negative"] = confirmation
    result = adapter.generate_image(
        {**image_request(), "negative_prompt": "text, labels"}
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "UNSUPPORTED_PARAMETER"
    assert result["scene_revision"] == 3


@pytest.mark.parametrize("kind", ["text", "image"])
def test_each_adapter_works_without_other_configuration(gateway, kind):
    adapter, state = gateway
    if kind == "text":
        adapter.config = Config(text_model="text", output_dir=adapter.config.output_dir)
        result = adapter.plan_storyboard(plan_request())
        expected = "/v1/chat/completions"
    else:
        adapter.config = Config(
            image_model="image", output_dir=adapter.config.output_dir
        )
        result = adapter.generate_image(image_request())
        expected = "/api/v1/images/generations"
    assert result["status"] == "ok", result
    assert [r.url.path for r in state["calls"] if r.method == "POST"] == [expected]


def test_request_selection_requires_no_defaults_and_preserves_metadata(gateway):
    adapter, state = gateway
    adapter.config.text_model = None
    adapter.config.image_model = None
    adapter.config.text_info = None
    adapter.config.image_info = None
    adapter.config.model_info = {
        "image": ModelInfo(name="Image provenance", checkpoint="sha256:trusted")
    }
    text = adapter.plan_storyboard({**plan_request(), "model": "text"})
    assert text["status"] == "ok", text
    assert text["meta"]["model"]["name"] == "text"
    image = adapter.generate_image({**image_request(), "model": "image"})
    assert image["status"] == "ok", image
    assert image["image"]["model"] == "Image provenance"
    assert image["meta"]["model"]["checkpoint"] == "sha256:trusted"
    assert adapter.config.text_model is None and adapter.config.image_model is None
    assert adapter.generate_image(image_request())["error"]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize(
    "kind,selected",
    [
        ("text", "image"),
        ("image", "text"),
        ("text", "unknown"),
        ("image", "unknown"),
        ("text", "synthetic"),
        ("image", "ambiguous"),
    ],
)
def test_wrong_kind_unknown_synthetic_and_ambiguous_never_submit(
    gateway, kind, selected
):
    adapter, state = gateway
    state["models"] = [
        {"id": "text", "task_type": "ai.chat"},
        {"id": "image", "task_type": "ai.image"},
        {"id": "synthetic", "task_type": "ai.chat", "synthetic": True},
        {"id": "ambiguous", "task_type": "ai.image"},
        {"id": "ambiguous", "task_type": "ai.image"},
    ]
    raw = plan_request() if kind == "text" else image_request()
    result = adapter.execute(kind, {**raw, "model": selected})
    assert result["status"] == "error" and result["error"]["code"] == "MODEL_NOT_LOADED"
    assert not any(r.method == "POST" for r in state["calls"])


def test_selected_image_models_have_distinct_idempotency_and_files(gateway):
    adapter, state = gateway
    state["models"] = [
        {"id": "image", "task_type": "ai.image", "name": "First"},
        {"id": "image2", "task_type": "ai.image", "name": "Second"},
    ]
    first = adapter.generate_image(image_request())
    explicit = adapter.generate_image({**image_request(), "model": "image"})
    second = adapter.generate_image({**image_request(), "model": "image2"})
    assert first["status"] == explicit["status"] == second["status"] == "ok"
    posts = [r for r in state["calls"] if r.method == "POST"]
    assert [json.loads(r.content)["model"] for r in posts] == [
        "image",
        "image",
        "image2",
    ]
    assert posts[0].headers["Idempotency-Key"] == posts[1].headers["Idempotency-Key"]
    assert posts[0].headers["Idempotency-Key"] != posts[2].headers["Idempotency-Key"]
    assert (
        first["image"]["path"] == explicit["image"]["path"] != second["image"]["path"]
    )
    assert second["image"]["model"] == second["meta"]["model"]["name"] == "Second"
    assert adapter.config.image_model == "image"


def test_concurrent_text_selection_is_request_local(gateway):
    from concurrent.futures import ThreadPoolExecutor

    adapter, state = gateway
    state["models"] = [
        {"id": "text", "task_type": "ai.chat"},
        {"id": "text2", "task_type": "ai.chat", "name": "Second text"},
    ]
    requests = [{**plan_request(), "model": model} for model in ("text", "text2") * 4]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(adapter.plan_storyboard, requests))
    assert all(r["status"] == "ok" for r in results)
    assert [r["meta"]["model"]["name"] for r in results] == [
        "Qwen text",
        "Second text",
    ] * 4
    posts = [r for r in state["calls"] if r.method == "POST"]
    keys = {}
    for post in posts:
        model = json.loads(post.content)["model"]
        keys.setdefault(model, set()).add(post.headers["Idempotency-Key"])
    assert len(keys["text"]) == len(keys["text2"]) == 1
    assert keys["text"] != keys["text2"]
    assert adapter.config.text_model == "text"


def test_http_catalogue_and_selected_describe_do_not_load_models(gateway):
    adapter, state = gateway
    state["models"] = [
        {"id": "text", "task_type": "ai.chat", "online": True},
        {"id": "image2", "name": "Image Two", "task_type": "ai.image", "online": False},
        {"id": "synthetic", "task_type": "ai.chat", "synthetic": True},
    ]
    token = "test-token-" * 4
    with TestClient(create_gateway(adapter, token)) as client:
        headers = {"Authorization": "Bearer " + token}
        rows = client.get("/v1/models", headers=headers).json()["data"]
        assert [r["id"] for r in rows] == ["text", "image2"]
        assert rows[0]["default"] and not rows[1]["default"]
        result = client.get("/v1/adapters/image?model=image2", headers=headers).json()
        assert result["model"]["name"] == "Image Two" and result["ready"] is False
        assert client.get("/v1/models").json()["status"] == "error"
        result = client.post(
            "/v1/images:generate",
            headers=headers,
            json={**image_request(), "model": "image2"},
        ).json()
        assert result["status"] == "ok", result
    assert len([r for r in state["calls"] if r.method == "POST"]) == 1


def test_catalogue_pagination_reaches_selected_model(gateway):
    adapter, state = gateway
    state["models"] = [
        {"id": "text" + str(i), "task_type": "ai.chat"} for i in range(101)
    ]
    result = adapter.plan_storyboard({**plan_request(), "model": "text100"})
    assert result["status"] == "ok", result
    assert json.loads(state["calls"][-1].content)["model"] == "text100"
    assert adapter.describe("text", "text100")["model"]["name"] == "text100"


@pytest.mark.parametrize("bad", ["", "x" * 101, True, 123, []])
def test_invalid_model_selector_is_rejected(gateway, bad):
    adapter, state = gateway
    result = adapter.generate_image({**image_request(), "model": bad})
    assert result["error"]["code"] == "INVALID_INPUT"
    assert not state["calls"]


def test_recorded_schema_echo_is_rejected_with_job_correlation(gateway):
    adapter, state = gateway
    state["board"] = adapter.schema
    result = adapter.plan_storyboard(plan_request())
    assert result["status"] == "error"
    assert "storyboard" not in result
    assert result["error"]["code"] == "SCHEMA_VALIDATION_FAILED"
    assert result["error"]["details"] == {"reason": "schema_echo", "job_id": "f" * 32}
    assert result["error"]["retryable"] is True


def test_generation_prompt_separates_constraints_from_instance_task(gateway):
    from pcc_server.text2visual import Plan
    from jsonschema import Draft202012Validator

    adapter, state = gateway
    before = json.dumps(adapter.schema, sort_keys=True)
    result = adapter.plan_storyboard(plan_request())
    assert result["status"] == "ok", result
    post = next(r for r in state["calls"] if r.method == "POST")
    system, user = json.loads(post.content)["messages"]
    assert "Do NOT copy or return the validation schema" in system["content"]
    assert "not a JSON Schema author" in system["content"]
    assert "OUTPUT_HEADER" in user["content"] and "SOURCE_REQUEST" in user["content"]
    assert "Water evaporates." in user["content"]
    assert '"$defs"' not in user["content"]
    assert '"schema_version": "1.0"' in user["content"]
    schema = json.loads(system["content"].rsplit("\n", 1)[1])
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(storyboard())
    scene = schema["$defs"]["scene"]
    assert {"image", "revision"} <= set(scene["required"])
    assert scene["properties"]["image"] == {"type": "null"}
    assert scene["properties"]["status"] == {"const": "planned"}
    assert "description" in schema["properties"]["style"]["properties"]
    assert "title" in scene["properties"]
    body = Plan.model_validate(plan_request())
    body.options.min_scenes = body.options.max_scenes = 4
    narrow = json.loads(
        adapter.planning_messages(body)[0]["content"].rsplit("\n", 1)[1]
    )
    assert narrow["properties"]["scenes"]["maxItems"] == 4
    assert json.dumps(adapter.schema, sort_keys=True) == before


@pytest.mark.parametrize("wrapper", ["json", "JSON", ""])
def test_single_json_fence_is_only_format_normalization(gateway, wrapper):
    adapter, state = gateway
    expected = storyboard()
    state["content"] = "```" + wrapper + "\n" + json.dumps(expected) + "\n```"
    result = adapter.plan_storyboard(plan_request())
    assert result["status"] == "ok", result
    assert result["storyboard"] == expected


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        "Explanation before JSON {}",
        "{} {}",
        "```json\n{}\n``` trailing text",
        None,
    ],
)
def test_malformed_content_reports_reason_without_raw_text(gateway, content):
    adapter, state = gateway
    state["content"] = content
    result = adapter.plan_storyboard(plan_request())
    assert result["error"]["code"] == "SCHEMA_VALIDATION_FAILED"
    assert result["error"]["details"]["reason"] in {"invalid_json", "missing_content"}
    assert result["error"]["details"]["job_id"] == "f" * 32
    assert "not JSON" not in json.dumps(result["error"]["details"])


def test_schema_and_semantic_errors_report_bounded_details(gateway):
    adapter, state = gateway
    state["board"]["scenes"][0]["order"] = "wrong"
    result = adapter.plan_storyboard(plan_request())
    details = result["error"]["details"]
    assert details["reason"] == "schema_mismatch"
    assert {"path": "/scenes/0/order", "rule": "type"} in details["issues"]
    assert len(details["issues"]) <= 8
    assert "wrong" not in json.dumps(details)
    state["board"] = storyboard()
    state["board"]["scenes"][0]["revision"] = 9
    assert (
        adapter.plan_storyboard(plan_request())["error"]["details"]["reason"]
        == "scene_initial_state_or_references"
    )
