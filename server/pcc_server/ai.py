import asyncio
import json
import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select
from .db import get_db, transaction
from .jobs import Submit, submit_job, view
from .models import Job, Node, TaskTemplate
from .schemas import Strict
from .security import current_user, require_scope
from .templates import accessible

router = APIRouter()


class ChatRequest(Strict):
    model: str = Field(max_length=100)
    messages: list[dict] = Field(min_length=1, max_length=64)
    stream: bool = False
    parameters: dict = Field(default_factory=dict)


class ImageRequest(Strict):
    model: str = Field(max_length=100)
    prompt: str = Field(min_length=1, max_length=2000)
    size: str | None = None
    images: list[str] = Field(default_factory=list, max_length=4)
    seed: int | None = Field(default=None, strict=True, ge=0, le=2**53 - 1)


def find_model(db, model, kind, user):
    template = next(
        (
            t
            for t in db.scalars(select(TaskTemplate).where(TaskTemplate.kind == kind))
            if t.config.get("model") == model and accessible(user, t)
        ),
        None,
    )
    if not template:
        raise HTTPException(404, "模型不存在或未授权")
    return template


@router.get("/api/v1/models")
def models(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "models:read")
    nodes = db.scalars(select(Node)).all()
    templates = [
        t
        for t in db.scalars(select(TaskTemplate).order_by(TaskTemplate.id))
        if t.kind.startswith("ai.") and accessible(user, t)
    ]
    return {
        "data": [
            {
                "id": t.config["model"],
                "object": "model",
                "owned_by": "PCC",
                "task_type": t.kind,
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
                    "size": body.size
                    or template.config.get("image_parameters", {}).get(
                        "default_size", "512x512"
                    ),
                    **({"images": body.images} if body.images else {}),
                    **({"seed": body.seed} if body.seed is not None else {}),
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
    body: ChatRequest, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:write")
    settings = request.app.state.settings
    with transaction(db):
        template = find_model(db, body.model, "ai.chat", user)
        job = submit_job(
            db,
            Submit(
                task_type="ai.chat",
                template_id=template.id,
                input={
                    "messages": body.messages,
                    **({"parameters": body.parameters} if body.parameters else {}),
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
    completion_id = "chatcmpl-" + job_id

    async def wait_result():
        deadline = time.monotonic() + settings.chat_wait_seconds
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return None
            with request.app.state.sessions() as poll:
                row = poll.get(Job, job_id)
                if row.status == "completed":
                    return row.result.get("content", "")
                if row.status in {"failed", "cancelled", "reconciling"}:
                    raise HTTPException(503, "推理执行失败或待核对；任务 ID：" + job_id)
            await asyncio.sleep(0.2)
        raise HTTPException(504, "等待推理超时；任务仍可在面板核对：" + job_id)

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
