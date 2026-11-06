# Text2Visual v0.1 compatibility gateway

Author: FubukiHanasaki

This branch adds an independent gateway for the original adapter-contracts v0.1. Existing PCC routes, databases, node configuration, models and grants are unchanged. No database migration is introduced. The supplied storyboard.schema.json v1.0 is packaged unchanged with the server.

## Interfaces

| HTTP | Python | Behaviour |
| --- | --- | --- |
| POST `/v1/storyboards:plan` | `TextAdapter.plan_storyboard(dict)` | Calls the configured PCC text model; validates JSON output against the supplied schema and essential identifier/revision/reference invariants. |
| POST `/v1/images:generate` | `ImageAdapter.generate_image(dict)` | Submits an authorized PCC image task, waits, verifies/downloads the artifact and returns an adapter-local path. |
| GET `/v1/adapters/text` | `TextAdapter.describe()` | Reports text adapter metadata and model visibility/online state. |
| GET `/v1/adapters/image` | `ImageAdapter.describe()` | Reports image adapter metadata and limitations. |

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

Edit `tmp/text2visual.json`: set the exact two authorized model IDs and truthful model metadata. Unknown provenance stays null. The example's model names are placeholders, not a model installation. Output files persist under the configured directory; use durable storage and back it up if scene history must survive deployment cleanup. Do not allow an untrusted account to modify that directory or configuration. The gateway does not automatically purge project images.

Provide secrets through environment variables, never URL/query parameters or tracked JSON files. Use a dedicated least-privilege PCC API key with `models:read`, `jobs:write`, `jobs:read` and access to the two templates. The gateway token protects a shared upstream identity: distribute it only to the trusted orchestrator, not directly to untrusted end users. It does not implement per-user authorization within that identity.

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

Send the original contract's JSON bodies to the POST endpoints. No PCC-specific fields need to be added by the orchestrator.

Python callers instantiate `TextAdapter(config, api_key)` or `ImageAdapter(config, api_key)` with a `Config` object. Call `adapter.client.close()` when finished. The shared `Adapter` class also provides both generation methods and `describe(kind)`.

## Original-contract limitations with current PCC

- **Image seed:** PCC currently has an administrator-maintained image seed, not a per-request override. `seed: null` selects that configured seed and returns it; an explicit matching seed is accepted; a different seed returns `UNSUPPORTED_PARAMETER`. `describe` reports `seed: false` / `per_request_seed: false`. A new seed cannot currently be used to force regeneration. This limitation requires a later PCC/Agent extension for full seed-controlled regeneration.
- **Negative prompt:** a nonempty per-request negative prompt returns `UNSUPPORTED_PARAMETER`. Existing locally approved workflow conditioning is preserved; this gateway does not modify the graph or backend parameters. Image `options` must currently be empty.
- **Reference images:** only PCC-supported bounded `data:image/png|jpeg|webp;base64,...` inputs are accepted, never URLs or filesystem paths. The approved node workflow must actually support the supplied reference. Because the catalogue cannot confirm that binding, describe conservatively reports `style_reference: false`; do not rely on reference-image generation without a successful deployment test. Text planning does not accept a style-reference image.
- **Provenance:** model name/version/licence/checkpoint/source URL are operator-supplied trusted metadata. Load time, device and peak VRAM are null because PCC does not expose those measurements. Nothing is inferred from a filename or fabricated.
- **Text:** the source is already extracted blocks; this gateway does not parse PDF/DOCX files. Source text is capped at 30,000 characters; the chosen model's actual context/output limits remain administrator-controlled and may be lower. Provide enough output tokens for 4–8 scenes. The gateway validates schema, identity, scene count/order, ID uniqueness, knowledge-point references and basic source-range ordering. It cannot prove factual grounding. The original schema has no block ID in source_ref, so ambiguous cross-block offsets remain a contract limitation.
- **Sync deadlines:** total wait is configurable (default 300 s), including queue/cold start. The gateway does not cancel jobs on timeout or automatically retry model failures. Image timeout after submission normally includes job_id; identical request bodies reuse a deterministic upstream idempotency key. Network timeouts can occur before an acknowledgement and thus before the gateway knows the job ID. For a new deliberate generation use a new request_id (same seed is still selected). Text calls also carry deterministic idempotency keys.
- **Files:** outputs are server-generated hashed absolute paths, not caller-selected project paths. The orchestrator must share the configured output volume or copy results through its own serving layer. Artifact download URLs from upstream are ignored; downloads use a fixed PCC route and size/hash/PNG checks. Files are atomically written with private permissions.
- **Metadata timing:** total_ms/latency_ms include queueing, transfer and adapter work, not GPU-only generation time. Output timestamps are UTC with an explicit offset.

## Verification

Run `tmp/venv/bin/python -m pytest tests/test_text2visual.py -q` after installing test extras. Tests cover original request/response envelopes, malformed model JSON/scene invariants, task polling, idempotency, metadata, unsupported parameters, integrity failures and gateway authentication. These are contract tests; they do not certify target-model generation quality or a live Windows deployment. Final acceptance needs real text/image jobs on the target server and its configured Windows node.
