# Text2Visual v0.1 compatibility gateway

Author: FubukiHanasaki

This branch adds an independent gateway for the original adapter-contracts v0.1. Existing PCC routes, databases, node configuration, models and grants are unchanged. No database migration is introduced. The supplied storyboard.schema.json v1.0 is packaged unchanged with the server.

## Interfaces

| HTTP | Python | Behaviour |
| --- | --- | --- |
| POST `/v1/storyboards:plan` | `TextAdapter.plan_storyboard(dict)` | Calls the request-selected PCC text model (or configured default); validates JSON output against the supplied schema and essential identifier/revision/reference invariants. |
| POST `/v1/images:generate` | `ImageAdapter.generate_image(dict)` | Submits an authorized PCC image task, waits, verifies/downloads the artifact and returns an adapter-local path. |
| GET `/v1/adapters/text?model=...` | `TextAdapter.describe(model=None)` | Reports text adapter metadata and model visibility/online state. |
| GET `/v1/adapters/image?model=...` | `ImageAdapter.describe(model=None)` | Reports image adapter metadata and limitations. |

GET `/v1/models` lists authorized non-synthetic model IDs, kinds, names, online status and defaults. Discovery/describe only read metadata and never load or prewarm models.

### Independent calls and per-request model selection

Both POST request bodies accept an optional top-level `model` containing the exact PCC model ID. For example, add `"model": "pcc-qwen3.5-9b:q5_k_m"` to a planning request, or `"model": "qwen-image-2.1"` / `"model": "sdxl-lightning-4step"` to an image request. All other original request and response fields remain unchanged. Requests go through the existing PCC authorization, resource protection and on-demand scheduling; the gateway never chooses another model automatically.

- A text request only submits text inference. It does not require an image default/configuration, generate images, or load an image model.
- An image request only submits image inference. It does not require a text default/configuration, call the text model, or plan a storyboard.
- Omitted/null model uses that kind's configured default. If no default exists, the request must specify model; no first-catalogue or 27B fallback occurs.
- `text_model`, `image_model`, `text_info`, `image_info` are now optional/null in configuration. Existing paired/default profiles continue to work. You can retain the currently deployed 9B default and select other authorized models per call without restarting or generating profile combinations.
- Configuring only `text_model` or only `image_model` is supported. The other kind can still be called with an explicitly selected authorized model. Omitting a default does not disable/revoke access to that kind; PCC grants remain the access boundary.
- `model_info` is an optional administrator metadata map keyed by exact model ID. Overrides apply only after the selected model is authorized. Otherwise a selected default retains its legacy text_info/image_info, or the gateway uses the PCC display name/ID and null provenance. Responses and saved-image metadata reflect the selected model, not another default.
- Wrong-kind, unauthorized, synthetic or ambiguous model IDs fail before task submission. Offline models can remain queued by PCC; there is no automatic substitution.
- Idempotency and local output hashes include selected model and kind. Default and explicit selection of the same model share a key; different models never share a key/file, including concurrent calls. Configuration is not mutated per request.

The original four endpoints are retained; the model selector and catalogue are optional extensions. The Python adapters accept the same `model` request field. `describe(model=...)` inspects that selected model without changing the default.

Generation returns HTTP 200 with `status: ok/error`, `request_id`, and `meta`; valid image requests also echo `scene_id` and `scene_revision` on success or failure. Expected input/backend errors are converted to the original error envelope. Describe responses use the original describe shape. Unrecognized routes/methods retain HTTP routing errors. Oversized bodies are rejected with an INVALID_INPUT envelope. Errors before JSON parsing cannot echo unparsed correlation fields.

No production mock model or fabricated inference results are provided. Test fixtures are deterministic substitutes used only for tests. Synthetic PCC model templates are explicitly rejected. `ready` means an authorized non-synthetic model is advertised online, not that weights are already resident or sufficient resources are currently free.

## Server trial deployment

Use a separate checkout so the running main-branch server is not replaced. Install from `codex/text2visual-adapter`, run the existing PCC server normally, then run this additional process on loopback port 8010. The gateway calls the existing PCC HTTP API; it does not need database credentials.

```bash
git fetch origin codex/text2visual-adapter
git worktree add ../personal_compute_cloud-text2visual origin/codex/text2visual-adapter
cd ../personal_compute_cloud-text2visual
mkdir -p tmp
UV_PROJECT_ENVIRONMENT=tmp/venv UV_CACHE_DIR=tmp/uv-cache uv sync --frozen
cp deploy/text2visual.example.json tmp/text2visual.json
```

Edit `tmp/text2visual.json`: optionally set a text default, image default, or both, and truthful model metadata. The example leaves both defaults null, requiring model selection in POST bodies. Unknown provenance stays null. Existing deployed configuration files can be retained unchanged. Output files persist under the configured directory; use durable storage and back it up if scene history must survive deployment cleanup. Do not allow an untrusted account to modify that directory or configuration. The gateway does not automatically purge project images.

Provide secrets through environment variables, never URL/query parameters or tracked JSON files. Use a dedicated least-privilege PCC API key with `models:read`, `jobs:write`, `jobs:read` and access to the templates you want callers to select. The gateway token protects a shared upstream identity: distribute it only to the trusted orchestrator, not directly to untrusted end users. It does not implement per-user authorization within that identity.

```bash
read -rsp 'PCC API key: ' PCC_ADAPTER_API_KEY
export PCC_ADAPTER_API_KEY
read -rsp 'Gateway token (at least 32 characters): ' PCC_ADAPTER_TOKEN
export PCC_ADAPTER_TOKEN
tmp/venv/bin/pcc-text2visual --config tmp/text2visual.json --port 8010
```

For PM2, from that checkout after configuring secrets in the same shell:

```bash
PM2_HOME=/root/personal_compute_cloud/tmp/pm2 pm2 start "$PWD/tmp/venv/bin/pcc-text2visual" --name pcc-text2visual --interpreter none -- --config "$PWD/tmp/text2visual.json" --port 8010
```

PM2 environment/dump files can contain secrets; protect the existing PM2 home. No Caddy, certificate, firewall or TLS configuration is changed. For a remote trial use an SSH tunnel to 8010, or the existing trusted serving layer. The gateway deliberately binds only 127.0.0.1. Remote PCC upstreams require HTTPS with normal certificate verification; redirects and environment proxies are disabled.

With `PCC_ADAPTER_TOKEN` available to the test shell:

```bash
curl -fsS -H "Authorization: Bearer $PCC_ADAPTER_TOKEN" http://127.0.0.1:8010/v1/adapters/image
```

Send the original contract's JSON bodies to the POST endpoints. The original bodies still work when defaults are configured; add model to select a particular backend per request.

Python callers instantiate `TextAdapter(config, api_key)` or `ImageAdapter(config, api_key)` with a `Config` object. Call `adapter.client.close()` when finished. The shared `Adapter` class also provides both generation methods, `describe(kind, model=None)` and `catalogue()`.

## Original-contract limitations with current PCC

- **Image seed:** the main-branch PCC/Agent seed extension is included. An explicit seed is forwarded unchanged; null/omission uses the administrator default. The actual completed-task result seed is returned and verified; missing/mismatched confirmation is rejected. Update both PCC and the image Agent, and configure an approved KSampler binding. `describe` reports per-request seed support.

- **Negative prompt:** negative_prompt is forwarded to PCC and the completed task must confirm the exact applied text. Empty text clears conditioning text; the original adapter contract does not accept null here. Update both PCC and Agent. SD workflows need negative_prompt_node and sampler_node; Qwen-Image 2.1 can reuse the combined text encoder with the correct sampler negative output. Missing bindings or missing/mismatched completion confirmation fail explicitly. Image options must currently be empty. See docs/agent.md for binding examples.
- **Reference images:** only PCC-supported bounded `data:image/png|jpeg|webp;base64,...` inputs are accepted, never URLs or filesystem paths. The approved node workflow must actually support the supplied reference. Because the catalogue cannot confirm that binding, describe conservatively reports `style_reference: false`; do not rely on reference-image generation without a successful deployment test. Text planning does not accept a style-reference image.
- **Provenance:** model name/version/licence/checkpoint/source URL are operator-supplied trusted metadata. Load time, device and peak VRAM are null because PCC does not expose those measurements. Nothing is inferred from a filename or fabricated.
- **Text:** the source is already extracted blocks; this gateway does not parse PDF/DOCX files. Source text is capped at 30,000 characters; the chosen model's actual context/output limits remain administrator-controlled and may be lower. Provide enough output tokens for 4–8 scenes. The gateway validates schema, identity, scene count/order, ID uniqueness, knowledge-point references and basic source-range ordering. It cannot prove factual grounding. The original schema has no block ID in source_ref, so ambiguous cross-block offsets remain a contract limitation.
- **Sync deadlines:** total wait is configurable (default 300 s), including queue/cold start. The gateway does not cancel jobs on timeout or automatically retry model failures. Image timeout after submission normally includes job_id; identical request bodies reuse a deterministic upstream idempotency key. Network timeouts can occur before an acknowledgement and thus before the gateway knows the job ID. For a new deliberate generation use a new request_id (null selects the administrator default). Text calls also carry deterministic idempotency keys.
- **Files:** outputs are server-generated hashed absolute paths, not caller-selected project paths. The orchestrator must share the configured output volume or copy results through its own serving layer. Artifact download URLs from upstream are ignored; downloads use a fixed PCC route and size/hash/PNG/dimension checks. Files are atomically written with private permissions.
- **Metadata timing:** total_ms/latency_ms include queueing, transfer and adapter work, not GPU-only generation time. Output timestamps are UTC with an explicit offset.

## Verification

Run `tmp/venv/bin/python -m pytest tests/test_text2visual.py -q` after installing test extras. Tests cover original request/response envelopes, malformed model JSON/scene invariants, task polling, idempotency, metadata, unsupported parameters, integrity failures and gateway authentication. These are contract tests; they do not certify target-model generation quality or a live Windows deployment. Final acceptance needs real text/image jobs on the target server and its configured Windows node.

Local verification (2026-11-06): main seed implementation passed 233 tests with 15 environment-specific skips; the combined compatibility branch passed 257 tests with the same 15 skips. A real PCC API/SQLite test exercised submission, lease claim, artifact upload, completion, polling and download with a deterministic worker result. Wheel/sdist checks confirmed the gateway module, unchanged schema asset and CLI entry point are packaged. Existing TestClient emitted one dependency deprecation warning; no background-thread warnings occurred. PostgreSQL restore/HTTPS and native Windows checks were skipped in this run; live model/server acceptance remains pending.

Negative-prompt follow-up (2026-11-06): main `6c704a1` passed 257 tests; the synchronized adapter passed 288 tests, with 15 environment-specific skips in each run. The actual PCC submission/lease/artifact test now sends a nonempty negative prompt and checks completion confirmation; a separate ComfyUI transport test checks the conditioning text in the submitted graph and returned result. Browser verification passed the Agent negative-node configuration save roundtrip and existing UI flows. Updated wheel assets were checked. No target Windows/model inference or production deployment was performed.


### Updating an existing trial deployment

Drain/finish in-flight gateway requests before replacing code. Model-aware idempotency keys use a new format; resubmitting an old request after this upgrade can create a new task. Do not replay completed requests merely to verify an upgrade. In the existing detached worktree, fetch `origin codex/text2visual-adapter`, fast-forward to `origin/codex/text2visual-adapter`, install with `UV_PROJECT_ENVIRONMENT=tmp/venv UV_CACHE_DIR=tmp/uv-cache uv sync --frozen`, then restart only `pcc-text2visual` through the existing PM2 home. Preserve tmp/text2visual.json and secrets; a 9B default needs no change. No main-server, Agent, Caddy or database changes are required for model selection. Check GET /v1/models and selected describe first; generate only against the intentionally selected model.

Model-selection verification (2026-11-06): 50 adapter tests passed, including text-only/image-only/default-free configuration, explicit and default selection against real PCC submission/lease/artifact routes with deterministic worker completions, wrong-kind/unknown/synthetic/ambiguous rejection, catalogue pagination, metadata correctness, per-model idempotency/output separation and concurrent text calls. Full SQLite regression passed 307 tests with 15 environment-specific skips. Wheel assets and the default-free example configuration were verified. No production requests, real model inference, or 27B model loading were performed during this update.

### Storyboard instance prompting and validation diagnostics

A live 9B trial returned the JSON Schema itself rather than a storyboard. Planning now places a compact, generation-specific validation constraint in the system message and a separate source/request plus exact output header in the user message. It explicitly asks for a JSON instance with source-specific key points, captions and image prompts, never a schema. The generation constraint narrows initial scenes to planned/revision 1/image null and the requested scene count. The original supplied schema is unchanged and still validates the actual result. No placeholder scene content is generated by the gateway, no validation is bypassed, and no automatic extra inference/retry is added.

A single enclosing JSON Markdown fence is removed as formatting only; surrounding prose, concatenated objects and malformed JSON remain errors. SCHEMA_VALIDATION_FAILED details now include a reason (schema_echo, invalid_json, missing_content, schema_mismatch, identity_mismatch, scene_count_order_or_ids, scene_initial_state_or_references or source_range). Where available, details.job_id comes from the PCC completion ID. Schema errors expose at most eight bounded paths/rule names, never raw generated text or source excerpts. Original raw content remains in the authorized PCC task record for inspection.

After updating this planner prompt, use a new request_id for deliberate re-generation. An old request ID remains associated with its original upstream idempotency key/result and must not be reused to request changed inference. Real model adherence still needs a new live 9B trial; fixture/schema tests alone do not establish target-model generation success.

Prompt-fix verification (2026-11-06): the supplied live output was parsed locally and confirmed to equal the original schema exactly. That schema-echo response is now an explicit regression case. Adapter tests passed 61 cases; full SQLite regression passed 318 with 15 environment-specific skips. Tests check compact generation constraints, unchanged original schema, exact valid instances, fenced JSON formatting, malformed output rejection, bounded diagnostics and task correlation. Package verification passed. No real-model call was made during the fix; a fresh live 9B request ID is required after deploying it.
