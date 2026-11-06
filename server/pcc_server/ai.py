import asyncio
import base64
import json
import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi import Response
from typing import Literal
from pydantic import Field
from sqlalchemy import select
from .db import get_db, transaction
from .jobs import Submit, submit_job, view, owned, artifact_data
from .models import Artifact, Job, Node, TaskTemplate
from .schemas import Strict
from .security import current_user, require_scope
from .templates import accessible

router = APIRouter()


class ChatRequest(Strict):
    model: str = Field(min_length=1, max_length=100)
    messages: list[dict] = Field(min_length=1, max_length=64)
    stream: bool = False
    parameters: dict = Field(default_factory=dict)
    temperature: float | None = Field(default=None, ge=0, le=2, strict=True)
    top_p: float | None = Field(default=None, gt=0, le=1, strict=True)
    top_k: int | None = Field(default=None, ge=1, le=200, strict=True)
    max_tokens: int | None = Field(default=None, ge=1, le=32768, strict=True)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=32768, strict=True)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1, strict=True)
    thinking: bool | None = Field(default=None, strict=True)

    def overrides(self):
        merged = dict(self.parameters)
        for key in (
            "temperature",
            "top_p",
            "top_k",
            "max_tokens",
            "max_completion_tokens",
            "seed",
            "thinking",
        ):
            value = getattr(self, key)
            if value is None:
                continue
            target = "max_tokens" if key == "max_completion_tokens" else key
            if target in merged and merged[target] != value:
                raise HTTPException(422, "重复的推理参数值不一致：" + target)
            if target not in merged:
                merged[target] = value
        return merged


class ImageRequest(Strict):
    model: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=2000)
    size: str | None = None
    images: list[str] = Field(default_factory=list, max_length=4)
    seed: int | None = Field(default=None, strict=True, ge=0, le=2**53 - 1)
    negative_prompt: str | None = Field(default=None, strict=True, max_length=2000)
    width: int | None = Field(default=None, ge=1, le=1536, strict=True)
    height: int | None = Field(default=None, ge=1, le=1536, strict=True)

    def dimensions(self, template):
        if (self.width is None) != (self.height is None):
            raise HTTPException(422, "width 和 height 必须同时指定")
        explicit = f"{self.width}x{self.height}" if self.width is not None else None
        if self.size is not None and explicit is not None and self.size != explicit:
            raise HTTPException(422, "size 与 width/height 不一致")
        return (
            self.size
            if self.size is not None
            else explicit
            or template.config.get("image_parameters", {}).get(
                "default_size", "512x512"
            )
        )


class ImageGenerationRequest(ImageRequest):
    n: int = Field(default=1, ge=1, le=1, strict=True)
    response_format: Literal["url", "b64_json"] = "url"


def find_model(db, model, kind, user):
    matches = [
        t
        for t in db.scalars(select(TaskTemplate).where(TaskTemplate.kind == kind))
        if t.config.get("model") == model and accessible(user, t)
    ]
    if not matches:
        raise HTTPException(404, "模型不存在或未授权")
    if len(matches) != 1:
        raise HTTPException(409, "模型 ID 对应多个已授权模板，请管理员使用唯一模型 ID")
    return matches[0]


@router.get("/v1/models")
@router.get("/api/v1/models")
def models(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    kind: Literal["text", "image"] | None = Query(None),
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "models:read")
    nodes = db.scalars(select(Node)).all()
    templates = [
        t
        for t in db.scalars(select(TaskTemplate).order_by(TaskTemplate.id))
        if t.kind.startswith("ai.")
        and accessible(user, t)
        and (kind is None or t.kind == ("ai.chat" if kind == "text" else "ai.image"))
    ]
    return {
        "object": "list",
        "data": [
            {
                "id": t.config["model"],
                "object": "model",
                "owned_by": "PCC",
                "task_type": t.kind,
                "kind": "text" if t.kind == "ai.chat" else "image",
                "name": t.name,
                "template_id": t.id,
                "parameters": t.config.get("parameters", {}),
                "image_parameters": t.config.get("image_parameters", {}),
                "synthetic": t.worker.startswith("synthetic."),
                "online": any(
                    not n.disabled
                    and time.time() - n.last_seen
                    < request.app.state.settings.heartbeat_timeout
                    and t.worker in n.capabilities.get("workers", [])
                    for n in nodes
                ),
            }
            for t in templates[offset : offset + limit]
        ],
        "limit": limit,
        "offset": offset,
    }


@router.post("/api/v1/images/generations", status_code=202)
def generate_image(
    body: ImageRequest, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:write")
    with transaction(db):
        template = find_model(db, body.model, "ai.image", user)
        job = submit_job(
            db,
            Submit(
                task_type="ai.image",
                template_id=template.id,
                input={
                    "prompt": body.prompt,
                    "size": body.dimensions(template),
                    **({"images": body.images} if body.images else {}),
                    **({"seed": body.seed} if body.seed is not None else {}),
                    **(
                        {"negative_prompt": body.negative_prompt}
                        if body.negative_prompt is not None
                        else {}
                    ),
                },
                timeout_seconds=template.config["max_timeout_seconds"],
            ),
            user,
            request.app.state.settings,
            request.headers.get("Idempotency-Key"),
        )
    return view(job, db, request.app.state.settings)


@router.post("/v1/chat/completions")
async def chat(
    body: ChatRequest,
    request: Request,
    response: Response,
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:write")
    settings = request.app.state.settings
    overrides = body.overrides()
    with transaction(db):
        template = find_model(db, body.model, "ai.chat", user)
        job = submit_job(
            db,
            Submit(
                task_type="ai.chat",
                template_id=template.id,
                input={
                    "messages": body.messages,
                    **({"parameters": overrides} if overrides else {}),
                },
                timeout_seconds=template.config["max_timeout_seconds"],
            ),
            user,
            settings,
            request.headers.get("Idempotency-Key"),
        )
        job_id = job.id
    # Do not hold a DB connection during inference or SSE streaming.
    db.close()
    response.headers["X-PCC-Job-ID"] = job_id
    response.headers["Cache-Control"] = "no-store"
    completion_id = "chatcmpl-" + job_id

    async def wait_result():
        result = await wait_for_job(request, job_id, settings.chat_wait_seconds)
        return result["result"].get("content", "")

    if not body.stream:
        content = await wait_result()
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content or ""},
                    "finish_reason": "stop",
                }
            ],
        }

    async def stream():
        try:
            content = await wait_result()
            if content is None:
                return
            # MVP streams a completed inference as chunks; no claim of backend token streaming.
            for offset in range(0, max(1, len(content)), 64):
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": body.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": content[offset : offset + 64]},
                            "finish_reason": None,
                        }
                    ],
                }
                yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "model": body.model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                )
                + "\n\n"
            )
        except HTTPException as exc:
            yield (
                "data: "
                + json.dumps(
                    {
                        "error": {
                            "code": str(exc.status_code),
                            "message": exc.detail,
                            "request_id": request.state.request_id,
                        }
                    }
                )
                + "\n\n"
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-PCC-Job-ID": job_id,
        },
    )


async def wait_for_job(request, job_id, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    headers = {"X-PCC-Job-ID": job_id}
    while time.monotonic() < deadline:
        if await request.is_disconnected():
            raise HTTPException(
                499, "请求已断开；任务仍可核对：" + job_id, headers=headers
            )
        with request.app.state.sessions() as db:
            row = db.get(Job, job_id)
            if row is None:
                raise HTTPException(404, "任务不存在", headers=headers)
            if row.status == "completed":
                return {"result": row.result or {}, "created_at": row.created_at}
            if row.status in {"failed", "cancelled", "reconciling"}:
                raise HTTPException(
                    503, "推理执行失败或待核对；任务 ID：" + job_id, headers=headers
                )
        await asyncio.sleep(0.2)
    raise HTTPException(504, "等待推理超时；任务仍可核对：" + job_id, headers=headers)


@router.post("/v1/images/generations")
async def image_generation(
    body: ImageGenerationRequest,
    request: Request,
    response: Response,
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:read")
    submitted = generate_image(body, request, user, db)
    job_id = submitted["job_id"]
    db.close()
    response.headers["X-PCC-Job-ID"] = job_id
    response.headers["Cache-Control"] = "no-store"
    completed = await wait_for_job(
        request, job_id, request.app.state.settings.image_wait_seconds
    )
    with request.app.state.sessions() as read:
        job = owned(read, job_id, user)
        artifact = read.scalar(
            select(Artifact)
            .where(
                Artifact.job_id == job_id,
                Artifact.attempt_id == job.current_attempt_id,
                Artifact.expires_at > time.time(),
                Artifact.name.endswith(".png"),
            )
            .order_by(Artifact.id)
        )
        if artifact is None:
            raise HTTPException(
                502, "已完成任务没有有效图片工件", headers={"X-PCC-Job-ID": job_id}
            )
        if artifact.size > request.app.state.settings.artifact_max_bytes:
            raise HTTPException(
                413, "图片超过当前工件大小限制", headers={"X-PCC-Job-ID": job_id}
            )
        item = {
            "artifact_id": artifact.id,
            "sha256": artifact.sha256,
            "expires_at": artifact.expires_at,
            "seed": completed["result"].get("seed"),
            "negative_prompt": completed["result"].get("negative_prompt"),
        }
        try:
            _, data = artifact_data(artifact.id, request, user, read)
        except HTTPException as exc:
            exc.headers = {**(exc.headers or {}), "X-PCC-Job-ID": job_id}
            raise
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(
                502, "工件不是 PNG 图片", headers={"X-PCC-Job-ID": job_id}
            )
        if body.response_format == "b64_json":
            item["b64_json"] = base64.b64encode(data).decode("ascii")
        else:
            item["url"] = str(request.url_for("download", artifact_id=artifact.id))
    return {"created": int(completed["created_at"]), "job_id": job_id, "data": [item]}
