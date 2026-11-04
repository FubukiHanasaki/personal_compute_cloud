"""Persistent asynchronous chat, scoped strictly to its user. Author: FubukiHanasaki."""

import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import select, func, delete
from .ai import find_model
from .db import get_db, transaction
from .jobs import Submit, submit_job, TERMINAL
from .models import Conversation, ChatTurn, Job
from .schemas import Strict
from .security import current_user, require_scope

router = APIRouter(prefix="/api/v1/conversations")


class Create(Strict):
    model: str = Field(min_length=1, max_length=100)


class Rename(Strict):
    title: str = Field(min_length=1, max_length=120)


class Send(Strict):
    content: str | list[dict]
    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{1,64}$")
    parameters: dict = Field(default_factory=dict)


def owned(db, identity, user, lock=False):
    query = select(Conversation).where(
        Conversation.id == identity, Conversation.owner_id == user.id
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(404, "对话不存在")
    return row


def summary(row):
    return {
        "id": row.id,
        "title": row.title,
        "model": row.model,
        "updated_at": row.updated_at,
    }


def turns(db, row):
    return db.execute(
        select(ChatTurn, Job)
        .join(Job, Job.id == ChatTurn.job_id)
        .where(ChatTurn.conversation_id == row.id)
        .order_by(ChatTurn.created_at, ChatTurn.id)
    ).all()


@router.get("")
def listing(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:read")
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.owner_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .offset(offset)
        .limit(limit)
    )
    return {"items": [summary(row) for row in rows]}


@router.post("", status_code=201)
def create(
    body: Create, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:write")
    with transaction(db):
        find_model(db, body.model, "ai.chat", user)
        if (
            db.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.owner_id == user.id)
            )
            >= 500
        ):
            raise HTTPException(429, "对话数量已达 500，请删除不再需要的对话")
        row = Conversation(owner_id=user.id, model=body.model)
        db.add(row)
        db.flush()
    return summary(row)


@router.get("/{identity}")
def detail(
    identity: str, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:read")
    row = owned(db, identity, user)
    return {
        **summary(row),
        "turns": [
            {
                "id": turn.id,
                "content": turn.content["content"],
                "job_id": job.id,
                "status": job.status,
                "progress": job.progress,
                "answer": (job.result or {}).get("content", ""),
                "error": job.error,
                "created_at": turn.created_at,
            }
            for turn, job in turns(db, row)
        ],
    }


@router.post("/{identity}/messages", status_code=202)
def send(
    identity: str,
    body: Send,
    request: Request,
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:write")
    with transaction(db):
        row = owned(db, identity, user, lock=True)
        old = db.scalar(
            select(ChatTurn).where(
                ChatTurn.conversation_id == row.id,
                ChatTurn.request_id == body.request_id,
            )
        )
        if old:
            if old.content != {"content": body.content, "parameters": body.parameters}:
                raise HTTPException(409, "重试标识已用于其他内容")
            return {"job_id": old.job_id}
        history = turns(db, row)
        if any(job.status not in TERMINAL for _, job in history):
            raise HTTPException(409, "请等待当前回复结束")
        if len(history) >= 31:
            raise HTTPException(422, "此对话已达 31 轮，请新建对话")
        template = find_model(db, row.model, "ai.chat", user)
        messages = []
        for turn, job in history:
            if job.status == "completed":
                messages.extend(
                    [
                        {"role": "user", "content": turn.content["content"]},
                        {
                            "role": "assistant",
                            "content": (job.result or {}).get("content", ""),
                        },
                    ]
                )
        messages.append({"role": "user", "content": body.content})
        job = submit_job(
            db,
            Submit(
                task_type="ai.chat",
                template_id=template.id,
                input={"messages": messages, "parameters": body.parameters},
                timeout_seconds=template.config["max_timeout_seconds"],
            ),
            user,
            request.app.state.settings,
        )
        db.add(
            ChatTurn(
                conversation_id=row.id,
                job_id=job.id,
                request_id=body.request_id,
                content={"content": body.content, "parameters": body.parameters},
            )
        )
        if not history:
            text = (
                body.content
                if isinstance(body.content, str)
                else " ".join(p.get("text", "") for p in body.content)
            )
            row.title = text.strip()[:60] or "图片对话"
        row.updated_at = time.time()
    return {"job_id": job.id}


@router.patch("/{identity}")
def rename(
    identity: str,
    body: Rename,
    request: Request,
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:write")
    with transaction(db):
        row = owned(db, identity, user, lock=True)
        row.title = body.title
    return summary(row)


@router.delete("/{identity}", status_code=204)
def remove(
    identity: str, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:write")
    with transaction(db):
        row = owned(db, identity, user, lock=True)
        if any(job.status not in TERMINAL for _, job in turns(db, row)):
            raise HTTPException(409, "请先结束对话中的任务")
        db.execute(delete(ChatTurn).where(ChatTurn.conversation_id == row.id))
        db.delete(row)
