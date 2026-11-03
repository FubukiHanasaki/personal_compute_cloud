import math
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import select
from .db import get_db, transaction
from .models import TaskTemplate, ServiceDefinition
from .schemas import Resources, Strict
from .security import admin, audit, current_user, require_scope

router = APIRouter()
KINDS = {"compute.stats": "compute.stats", "ai.chat": None, "ai.image": None}


class TemplateCreate(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    name: str = Field(min_length=1, max_length=120)
    kind: str
    worker: str = Field(max_length=120)
    resources: Resources = Resources(cpu=1, memory_mb=256)
    max_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    public: bool = False
    retry_safe: bool = False
    model: str | None = Field(default=None, max_length=100)
    idle_seconds: int = Field(default=300, ge=1, le=86400)


def accessible(user, template):
    return template.enabled and (
        user.role == "owner"
        or template.public
        or template.id in user.grants.get("templates", [])
    )


def validate_input(template, data):
    if template.kind == "compute.stats":
        if (
            set(data) != {"values"}
            or not isinstance(data["values"], list)
            or not 1 <= len(data["values"]) <= 10000
        ):
            raise HTTPException(422, "统计模板需要 1–10000 个数字")
        if any(
            type(v) not in {int, float} or abs(v) > 1e12 or not math.isfinite(v)
            for v in data["values"]
        ):
            raise HTTPException(422, "仅支持有限数字，绝对值不超过 1e12")
    elif template.kind == "ai.chat":
        if (
            set(data) != {"messages"}
            or not isinstance(data["messages"], list)
            or not 1 <= len(data["messages"]) <= 64
        ):
            raise HTTPException(422, "messages 无效")
        for msg in data["messages"]:
            if (
                not isinstance(msg, dict)
                or set(msg) != {"role", "content"}
                or not isinstance(msg["role"], str)
                or msg["role"] not in {"system", "user", "assistant"}
                or not isinstance(msg["content"], str)
                or len(msg["content"]) > 16000
            ):
                raise HTTPException(422, "不支持的消息格式")
    elif template.kind == "ai.image":
        if (
            set(data) != {"prompt", "size"}
            or not isinstance(data["prompt"], str)
            or not 1 <= len(data["prompt"]) <= 2000
            or not isinstance(data["size"], str)
            or data["size"] not in {"256x256", "512x512"}
        ):
            raise HTTPException(422, "图片参数无效")
    else:
        raise HTTPException(422, "未实现的模板类型")


@router.post("/api/v1/admin/templates", status_code=201)
def create_template(body: TemplateCreate, user=Depends(admin), db=Depends(get_db)):
    if user.role != "owner":
        raise HTTPException(403, "只有 Owner 可批准模板")
    if body.kind not in KINDS:
        raise HTTPException(422, "不允许任意代码或命令模板")
    if body.kind == "compute.stats" and body.worker != "compute.stats":
        raise HTTPException(422, "计算模板必须使用受控 Worker")
    if body.kind != "compute.stats" and not (
        body.worker in {"synthetic.chat", "synthetic.image"}
        or body.worker.startswith(("ollama:", "comfyui:"))
    ):
        raise HTTPException(422, "未支持的推理适配器")
    if (
        body.kind == "ai.chat"
        and body.worker != "synthetic.chat"
        and not body.worker.startswith("ollama:")
    ):
        raise HTTPException(422, "Chat 后端不匹配")
    if (
        body.kind == "ai.image"
        and body.worker != "synthetic.image"
        and not body.worker.startswith("comfyui:")
    ):
        raise HTTPException(
            422, "图片模板需要 synthetic.image 或本地批准的 ComfyUI 工作流"
        )
    if body.kind.startswith("ai.") and not body.model:
        raise HTTPException(422, "AI 模板需要显式模型 ID")
    if body.worker.startswith(("ollama:", "comfyui:")) and body.worker.split(":", 1)[
        1
    ] != str(body.model):
        raise HTTPException(422, "模型与后端能力必须一致")
    if body.resources.cpu < 1 or body.resources.memory_mb < 128:
        raise HTTPException(422, "资源限制不足")
    with transaction(db):
        if db.get(TaskTemplate, body.id):
            raise HTTPException(409, "模板 ID 已存在")
        row = TaskTemplate(
            id=body.id,
            name=body.name,
            kind=body.kind,
            worker=body.worker,
            config={
                "resources": body.resources.model_dump(),
                "max_timeout_seconds": body.max_timeout_seconds,
                "model": body.model,
            },
            public=body.public,
            retry_safe=body.retry_safe,
        )
        db.add(row)
        if body.kind.startswith("ai."):
            if db.scalar(
                select(ServiceDefinition).where(ServiceDefinition.id == body.id)
            ):
                raise HTTPException(409, "服务定义已存在")
            for definition in db.scalars(select(ServiceDefinition)):
                if definition.allowed_config.get("model") == body.model:
                    raise HTTPException(409, "模型 ID 必须唯一")
            db.add(
                ServiceDefinition(
                    id=body.id,
                    type=body.worker.split(":", 1)[0]
                    if ":" in body.worker
                    else "synthetic",
                    allowed_config={
                        "worker": body.worker,
                        "model": body.model,
                        "resources": body.resources.model_dump(),
                    },
                    idle_policy={"idle_seconds": body.idle_seconds},
                )
            )
        audit(db, user.id, "template.approve", row.id)
    return {"id": row.id}


@router.get("/api/v1/templates")
def list_templates(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:read")
    templates = [
        t
        for t in db.scalars(select(TaskTemplate).order_by(TaskTemplate.id))
        if accessible(user, t)
    ]
    return {
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "kind": t.kind,
                "worker": t.worker,
                "config": t.config,
            }
            for t in templates[offset : offset + limit]
        ],
        "limit": limit,
        "offset": offset,
    }
