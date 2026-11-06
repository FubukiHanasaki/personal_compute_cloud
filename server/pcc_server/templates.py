import math
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import select, func
from .db import get_db, transaction
from .models import TaskTemplate, ServiceDefinition, Job
from pcc_agent.inference import (
    parameters,
    ImageParameters,
    validate_messages,
    IMAGE_SIZES,
    image_bytes,
    image_seed,
)
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
    parameters: dict = Field(default_factory=dict)
    image_parameters: ImageParameters = ImageParameters()


class TemplateVisibility(Strict):
    public: bool


@router.put("/api/v1/admin/templates/{template_id}/visibility")
def template_visibility(
    template_id: str,
    body: TemplateVisibility,
    user=Depends(admin),
    db=Depends(get_db),
):
    if user.role != "owner":
        raise HTTPException(403, "只有 Owner 可修改模板开放范围")
    with transaction(db):
        row = db.get(TaskTemplate, template_id)
        if not row:
            raise HTTPException(404, "模板不存在")
        row.public = body.public
        audit(
            db,
            user.id,
            "template.visibility",
            row.id,
            "public" if body.public else "restricted",
        )
    return {"id": row.id, "public": row.public}


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
        if set(data) not in ({"messages"}, {"messages", "parameters"}):
            raise HTTPException(422, "messages 无效")
        try:
            defaults = getattr(template, "config", {}).get("parameters", {})
            validate_messages(data.get("messages"), defaults.get("vision", False))
            if "parameters" in data:
                overrides = data["parameters"]
                if not isinstance(overrides, dict) or set(overrides) - {
                    "temperature",
                    "top_p",
                    "top_k",
                    "max_tokens",
                    "seed",
                    "thinking",
                }:
                    raise ValueError(
                        "只能调整采样参数；上下文、KV 和视觉设置由管理员维护"
                    )
                adapter = template.worker.split(":")[0]
                effective = parameters({**defaults, **overrides}, adapter)
                if effective["max_tokens"] > defaults.get("max_tokens", 2048):
                    raise ValueError("输出上限超过管理员配置")
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
    elif template.kind == "ai.image":
        if (
            set(data) - {"prompt", "size", "images", "seed"}
            or not {"prompt", "size"} <= set(data)
            or not isinstance(data["prompt"], str)
            or not 1 <= len(data["prompt"]) <= 2000
            or not isinstance(data["size"], str)
            or data["size"] not in IMAGE_SIZES
        ):
            raise HTTPException(422, "图片参数无效")
        if getattr(template, "worker", "") == "synthetic.image" and (
            data["size"] not in {"256x256", "512x512"} or data.get("images")
        ):
            raise HTTPException(422, "协议测试图片仅支持 256 / 512 方图，不支持参考图")
        images = data.get("images", [])
        if not isinstance(images, list) or len(images) > 4:
            raise HTTPException(422, "最多 4 张参考图片")
        try:
            image_seed(data.get("seed"), 0)
            for image in images:
                image_bytes(image)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
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
        or body.worker.startswith(("ollama:", "comfyui:", "ninfer:"))
    ):
        raise HTTPException(422, "未支持的推理适配器")
    if (
        body.kind == "ai.chat"
        and body.worker != "synthetic.chat"
        and not body.worker.startswith(("ollama:", "ninfer:"))
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
    if body.worker.startswith(("ollama:", "comfyui:", "ninfer:")) and body.worker.split(
        ":", 1
    )[1] != str(body.model):
        raise HTTPException(422, "模型与后端能力必须一致")
    if body.resources.cpu < 1 or body.resources.memory_mb < 128:
        raise HTTPException(422, "资源限制不足")
    try:
        inference = (
            parameters(body.parameters, body.worker.split(":")[0])
            if body.kind == "ai.chat"
            else {}
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
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
                "parameters": inference,
                "image_parameters": body.image_parameters.model_dump(),
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
                        "parameters": inference,
                        "image_parameters": body.image_parameters.model_dump(),
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
        if accessible(user, t) or user.role in {"owner", "admin"}
    ]
    return {
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "kind": t.kind,
                "worker": t.worker,
                "config": {
                    **t.config,
                    "idle_seconds": (
                        db.get(ServiceDefinition, t.id).idle_policy["idle_seconds"]
                        if db.get(ServiceDefinition, t.id)
                        else 300
                    ),
                },
                "public": t.public,
            }
            for t in templates[offset : offset + limit]
        ],
        "limit": limit,
        "offset": offset,
    }


class ModelUpdate(Strict):
    name: str = Field(min_length=1, max_length=120)
    parameters: dict = Field(default_factory=dict)
    image_parameters: ImageParameters = ImageParameters()
    resources: Resources
    max_timeout_seconds: int = Field(ge=1, le=3600)
    idle_seconds: int = Field(ge=1, le=86400)


@router.put("/api/v1/admin/templates/{template_id}")
def update_template(
    template_id: str, body: ModelUpdate, user=Depends(admin), db=Depends(get_db)
):
    with transaction(db):
        row = db.scalar(
            select(TaskTemplate).where(TaskTemplate.id == template_id).with_for_update()
        )
        if not row:
            raise HTTPException(404, "模型不存在")
        # Serialize submission with edits. Never mutate settings of queued/running work.
        if db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.template_id == row.id,
                Job.status.not_in(["completed", "failed", "cancelled"]),
            )
        ):
            raise HTTPException(409, "模型仍有排队或运行中的任务，请结束后再更新参数")
        if body.resources.cpu < 1 or body.resources.memory_mb < 128:
            raise HTTPException(422, "资源限制不足")
        try:
            params = (
                parameters(body.parameters, row.worker.split(":")[0])
                if row.kind == "ai.chat"
                else {}
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        row.name = body.name
        row.config = {
            **row.config,
            "resources": body.resources.model_dump(),
            "max_timeout_seconds": body.max_timeout_seconds,
            "parameters": params,
            "image_parameters": body.image_parameters.model_dump(),
        }
        definition = db.get(ServiceDefinition, row.id)
        if definition:
            definition.allowed_config = {
                **definition.allowed_config,
                "resources": body.resources.model_dump(),
                "parameters": params,
                "image_parameters": body.image_parameters.model_dump(),
            }
            definition.idle_policy = {"idle_seconds": body.idle_seconds}
        audit(db, user.id, "model.parameters.update", row.id)
    return {"id": row.id}
