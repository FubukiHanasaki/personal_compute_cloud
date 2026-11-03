import asyncio
import base64
import hashlib
import json
import time
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import Field
from sqlalchemy import func, select
from .db import get_db, transaction
from .models import (
    Artifact,
    Job,
    JobAttempt,
    JobEvent,
    Node,
    TaskTemplate,
    ServiceInstance,
    ServiceDefinition,
    User,
    uid,
)
from .schemas import Resources, Strict
from .security import (
    admin,
    audit,
    can_manage,
    current_node,
    current_user,
    digest,
    require_scope,
)
from .storage import LocalStorage
from .templates import accessible, validate_input

router = APIRouter()
TERMINAL = {"completed", "failed", "cancelled"}
ACTIVE = {"claimed", "running", "cancel_requested", "reconciling"}


class Requirements(Strict):
    cpu: float = Field(default=0, ge=0, le=65536)
    memory_mb: int = Field(default=0, ge=0, le=2**30)
    vram_mb: int = Field(default=0, ge=0, le=2**30)
    gpu: bool = False
    min_vram_gb: float = Field(default=0, ge=0, le=1024)


class Submit(Strict):
    task_type: str = Field(max_length=40)
    template_id: str = Field(max_length=80)
    input: dict
    requirements: Requirements = Requirements()
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    allow_paid_cloud: bool = False


class Claim(Strict):
    claim_id: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    wait_seconds: int = Field(default=0, ge=0, le=20)


class Lease(Strict):
    attempt_id: str = Field(max_length=32)
    lease_token: str = Field(max_length=256)


class Completion(Lease):
    result: dict
    artifact_ids: list[str] = Field(default_factory=list, max_length=8)


class Failure(Lease):
    stopped: bool
    retryable: bool = False
    message: str = Field(max_length=300)


class Event(Lease):
    sequence: int = Field(ge=0, le=255)
    message: str = Field(max_length=4096)
    progress: float = Field(ge=0, le=1)


class Upload(Lease):
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")
    data_base64: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def view(job):
    return {
        "job_id": job.id,
        "status": job.status,
        "task_type": job.task_type,
        "template_id": job.template_id,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
        "requirements": job.requirements,
        "queue_reason": "等待满足模板能力、共享策略和资源额度的在线节点"
        if job.status == "queued"
        else None,
    }


def owned(db, job_id, user):
    job = db.get(Job, job_id)
    allowed = job and (
        job.owner_id == user.id
        or user.role == "owner"
        or (user.role == "admin" and job.id in user.grants.get("jobs", []))
    )
    if not allowed:
        raise HTTPException(404, "任务不存在或无权限")
    return job


def submit_job(db, body, user, settings, idem=None):
    if body.allow_paid_cloud:
        raise HTTPException(422, "付费云资源尚未启用")
    if idem and (len(idem) > 128 or not idem.isascii()):
        raise HTTPException(422, "幂等键无效")
    request_hash = fingerprint(body.model_dump())
    if idem:
        old = db.scalar(
            select(Job).where(Job.owner_id == user.id, Job.idempotency_key == idem)
        )
        if old:
            if old.request_hash != request_hash:
                raise HTTPException(409, "幂等键已用于不同请求")
            return old
    template = db.get(TaskTemplate, body.template_id)
    if (
        not template
        or not accessible(user, template)
        or template.kind != body.task_type
    ):
        raise HTTPException(403, "模板未授权或类型不匹配")
    validate_input(template, body.input)
    if len(json.dumps(body.input).encode()) > 100000:
        raise HTTPException(413, "任务输入过大")
    if body.timeout_seconds > template.config["max_timeout_seconds"]:
        raise HTTPException(422, "超时超过模板上限")
    count = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.owner_id == user.id, Job.status.not_in(TERMINAL))
    )
    if count >= settings.user_active_job_limit:
        raise HTTPException(429, "活动任务配额已满")
    daily = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.owner_id == user.id, Job.created_at >= time.time() - 86400)
    )
    if daily >= settings.user_daily_job_limit:
        raise HTTPException(429, "24 小时任务配额已满")
    requested = body.requirements.model_dump()
    requirements = {
        k: max(template.config["resources"][k], requested[k])
        for k in ("cpu", "memory_mb", "vram_mb")
    }
    requirements["vram_mb"] = max(
        requirements["vram_mb"],
        int(requested["min_vram_gb"] * 1024),
        1 if requested["gpu"] else 0,
    )
    if (
        requirements["cpu"] > 256
        or requirements["memory_mb"] > 1024 * 1024
        or requirements["vram_mb"] > 1024 * 1024
    ):
        raise HTTPException(422, "请求超出控制面资源上限")
    job = Job(
        owner_id=user.id,
        template_id=template.id,
        task_type=body.task_type,
        input=body.input,
        requirements=requirements,
        timeout_seconds=body.timeout_seconds,
        idempotency_key=idem,
        request_hash=request_hash,
    )
    db.add(job)
    db.flush()
    audit(db, user.id, "job.submit", job.id)
    return job


def sweep(db, settings):
    now = time.time()
    for attempt in db.scalars(
        select(JobAttempt).where(
            JobAttempt.status == "active", JobAttempt.lease_expires_at <= now
        )
    ):
        job = db.get(Job, attempt.job_id)
        if job.current_attempt_id == attempt.id and job.status not in TERMINAL:
            job.status = "reconciling"
            job.error = "执行租约过期，等待节点确认原执行已停止"
            job.updated_at = now
            attempt.status = "reconciling"
            audit(db, "server", "job.reconciling", job.id)


def policy_allows(policy, node, job):
    mode = policy.get("mode", "maintenance")
    if mode in {"maintenance", "offline"}:
        return False
    if mode == "private" and job.owner_id != node.owner_id:
        return False
    if mode == "reserved" and job.owner_id not in policy.get("allowed_users", []):
        return False
    return True


def eligible(node, job, template, settings, used, active_count, reclaimable=None):
    caps = node.capabilities
    if (
        node.disabled
        or time.time() - node.last_seen > settings.heartbeat_timeout
        or template.worker not in caps.get("workers", [])
    ):
        return False
    if template.worker in {
        "compute.stats",
        "synthetic.chat",
        "synthetic.image",
    } and "linux-bwrap-rlimit" not in caps.get("isolation", []):
        return False
    for policy in (node.policy, node.local_policy):
        if not policy_allows(policy, node, job) or active_count >= policy.get(
            "max_concurrency", 1
        ):
            return False
        if caps.get("cpu_load", 100) >= policy.get("max_cpu_load", 85) or caps.get(
            "memory_load", 100
        ) >= policy.get("max_memory_load", 90):
            return False
    for key, value in job.requirements.items():
        capacity = min(
            caps.get("allocatable", {}).get(key, 0),
            caps.get("physical", {}).get(key, 0),
        )
        available = min(
            caps.get("physical", {}).get(key, 0),
            caps.get("available", {}).get(key, 0)
            + (reclaimable or {}).get(node.id, {}).get(key, 0),
        )
        if capacity - used.get(key, 0) < value or available < value:
            return False
    return True


def lease_token(attempt, settings):
    return "lease_" + digest("lease:" + attempt.id + ":" + attempt.node_id, settings)


def claim_view(job, attempt, template, settings):
    return {
        "job_id": job.id,
        "owner_id": job.owner_id,
        "attempt_id": attempt.id,
        "lease_token": lease_token(attempt, settings),
        "lease_expires_at": attempt.lease_expires_at,
        "deadline": attempt.deadline,
        "server_time": time.time(),
        "task_type": job.task_type,
        "template": {
            "id": template.id,
            "worker": template.worker,
            "config": template.config,
        },
        "input": job.input,
        "limits": {
            **job.requirements,
            "timeout_seconds": job.timeout_seconds,
            "output_bytes": settings.artifact_max_bytes,
        },
    }


@router.post("/api/v1/jobs", status_code=202)
def submit(
    body: Submit, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:write")
    with transaction(db):
        job = submit_job(
            db,
            body,
            user,
            request.app.state.settings,
            request.headers.get("Idempotency-Key"),
        )
    return view(job)


@router.get("/api/v1/jobs")
def list_jobs(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:read")
    with transaction(db):
        sweep(db, request.app.state.settings)
        query = select(Job).order_by(Job.created_at.desc())
        if user.role != "owner":
            query = query.where(Job.owner_id == user.id)
        rows = db.scalars(query.offset(offset).limit(limit)).all()
    return {"items": [view(j) for j in rows], "limit": limit, "offset": offset}


@router.get("/api/v1/jobs/{job_id}")
def get_job(
    job_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:read")
    with transaction(db):
        sweep(db, request.app.state.settings)
        job = owned(db, job_id, user)
    return view(job)


@router.post("/api/v1/jobs/{job_id}/cancel", status_code=202)
def cancel(
    job_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:write")
    with transaction(db):
        job = owned(db, job_id, user)
        if job.status not in TERMINAL:
            job.status = "cancelled" if job.status == "queued" else "cancel_requested"
            job.updated_at = time.time()
            audit(db, user.id, "job.cancel", job.id)
    return view(job)


@router.post("/api/v1/nodes/{node_id}/abort", status_code=202)
def abort_node(node_id: str, user=Depends(admin), db=Depends(get_db)):
    with transaction(db):
        node = db.get(Node, node_id)
        if not node or not can_manage(user, node):
            raise HTTPException(404, "节点不存在或无权限")
        jobs = db.scalars(
            select(Job)
            .join(JobAttempt, Job.current_attempt_id == JobAttempt.id)
            .where(JobAttempt.node_id == node.id, Job.status.in_(ACTIVE))
        ).all()
        for job in jobs:
            job.status = "cancel_requested"
        audit(db, user.id, "node.abort_managed_jobs", node.id)
    return {"cancel_requested": len(jobs)}


def claim_once(body, node, db, settings):
    with transaction(db):
        sweep(db, settings)
        node = db.get(Node, node.id)
        if node.disabled:
            raise HTTPException(403, "节点已禁用")
        previous = db.scalar(
            select(JobAttempt).where(
                JobAttempt.node_id == node.id, JobAttempt.claim_id == body.claim_id
            )
        )
        if previous:
            job = db.get(Job, previous.job_id)
            if (
                previous.status != "active"
                or previous.lease_expires_at <= time.time()
                or job.status == "cancel_requested"
            ):
                raise HTTPException(409, "本次领取已结束或待核对")
            return claim_view(
                job, previous, db.get(TaskTemplate, job.template_id), settings
            )
        attempts = db.scalars(
            select(JobAttempt).where(JobAttempt.status.in_(["active", "reconciling"]))
        ).all()
        used = {
            n.id: {"cpu": 0, "memory_mb": 0, "vram_mb": 0}
            for n in db.scalars(select(Node))
        }
        counts = {n: 0 for n in used}
        for a in attempts:
            active_job = db.get(Job, a.job_id)
            for k in used[a.node_id]:
                used[a.node_id][k] += active_job.requirements[k]
            counts[a.node_id] += 1
        for instance in db.scalars(
            select(ServiceInstance).where(
                (ServiceInstance.active_requests > 0)
                | ServiceInstance.state.in_(["starting", "stopping", "error"])
            )
        ):
            represented = any(
                a.node_id == instance.node_id
                and db.get(Job, a.job_id).template_id == instance.definition_id
                for a in attempts
            )
            if not represented and instance.node_id in used:
                counts[instance.node_id] += 1
                resources = db.get(
                    ServiceDefinition, instance.definition_id
                ).allowed_config["resources"]
                for key in used[instance.node_id]:
                    used[instance.node_id][key] += resources[key]
        reclaimable = {}
        for instance in db.scalars(
            select(ServiceInstance).where(
                ServiceInstance.active_requests == 0,
                ServiceInstance.state.in_(["idle", "ready"]),
            )
        ):
            resources = db.get(
                ServiceDefinition, instance.definition_id
            ).allowed_config["resources"]
            budget = reclaimable.setdefault(
                instance.node_id, {"cpu": 0, "memory_mb": 0, "vram_mb": 0}
            )
            for key in budget:
                budget[key] += resources[key]
        for job in db.scalars(
            select(Job).where(Job.status == "queued").order_by(Job.created_at)
        ):
            template = db.get(TaskTemplate, job.template_id)
            instance = (
                db.scalar(
                    select(ServiceInstance).where(
                        ServiceInstance.node_id == node.id,
                        ServiceInstance.definition_id == template.id,
                    )
                )
                if template.kind.startswith("ai.")
                else None
            )
            if instance and instance.desired_state == "stopped":
                continue
            owner = db.get(User, job.owner_id)
            if not owner or owner.status != "active" or not accessible(owner, template):
                job.status = "failed"
                job.error = "提交者已被禁用或模板授权已撤销"
                job.updated_at = time.time()
                audit(db, "server", "job.authorization_revoked", job.id)
                continue
            if not template.enabled or not eligible(
                node,
                job,
                template,
                settings,
                used[node.id],
                counts[node.id],
                reclaimable,
            ):
                continue
            # Private/reserved nodes participate only when no eligible shared node exists.
            if (
                node.policy.get("mode") != "shared"
                or node.local_policy.get("mode") != "shared"
            ):
                if any(
                    n.id != node.id
                    and n.policy.get("mode") == "shared"
                    and n.local_policy.get("mode") == "shared"
                    and eligible(
                        n,
                        job,
                        template,
                        settings,
                        used[n.id],
                        counts[n.id],
                        reclaimable,
                    )
                    for n in db.scalars(select(Node))
                ):
                    continue
            now = time.time()
            attempt = JobAttempt(
                id=uid(),
                job_id=job.id,
                node_id=node.id,
                claim_id=body.claim_id,
                lease_token_hash="",
                lease_expires_at=min(
                    now + settings.lease_seconds, now + job.timeout_seconds
                ),
                deadline=now + job.timeout_seconds,
            )
            attempt.lease_token_hash = digest(lease_token(attempt, settings), settings)
            db.add(attempt)
            job.status = "claimed"
            job.current_attempt_id = attempt.id
            job.updated_at = now
            audit(db, node.id, "job.claim", job.id)
            payload = claim_view(job, attempt, template, settings)
            return payload
    return None


@router.post("/internal/v1/jobs/claim")
async def claim(
    body: Claim, request: Request, node=Depends(current_node), db=Depends(get_db)
):
    settings = request.app.state.settings
    until = time.monotonic() + body.wait_seconds
    while True:
        payload = await run_in_threadpool(claim_once, body, node, db, settings)
        if payload is not None:
            return payload
        if time.monotonic() >= until or await request.is_disconnected():
            return Response(status_code=204)
        await asyncio.sleep(0.25)


def checked_attempt(
    db, job_id, body, node, settings, allow_expired=False, allow_terminal=False
):
    job = db.get(Job, job_id)
    attempt = db.get(JobAttempt, body.attempt_id)
    if (
        not job
        or not attempt
        or attempt.job_id != job.id
        or attempt.node_id != node.id
        or job.current_attempt_id != attempt.id
        or attempt.lease_token_hash != digest(body.lease_token, settings)
    ):
        raise HTTPException(409, "执行尝试或租约不匹配")
    if not allow_terminal and job.status in TERMINAL:
        raise HTTPException(409, "任务已终结")
    if not allow_expired and (
        attempt.lease_expires_at <= time.time() or attempt.status != "active"
    ):
        raise HTTPException(409, "租约已过期，需确认执行停止")
    return job, attempt


@router.post("/internal/v1/jobs/{job_id}/renew")
def renew(
    job_id: str,
    body: Lease,
    request: Request,
    node=Depends(current_node),
    db=Depends(get_db),
):
    settings = request.app.state.settings
    with transaction(db):
        job, attempt = checked_attempt(db, job_id, body, node, settings)
        attempt.lease_expires_at = min(
            time.time() + settings.lease_seconds, attempt.deadline
        )
        if job.status == "claimed":
            job.status = "running"
        response = {
            "status": job.status,
            "lease_expires_at": attempt.lease_expires_at,
            "cancel_requested": job.status == "cancel_requested",
            "server_time": time.time(),
        }
    return response


@router.post("/internal/v1/jobs/{job_id}/complete")
def complete(
    job_id: str,
    body: Completion,
    request: Request,
    node=Depends(current_node),
    db=Depends(get_db),
):
    settings = request.app.state.settings
    completion_hash = fingerprint(body.model_dump(exclude={"lease_token"}))
    if len(json.dumps(body.result).encode()) > 65536:
        raise HTTPException(413, "结果元数据过大，请上传工件")
    with transaction(db):
        job, attempt = checked_attempt(
            db, job_id, body, node, settings, allow_expired=True, allow_terminal=True
        )
        if job.status == "completed" and attempt.completion_hash == completion_hash:
            return view(job)
        if (
            job.status in TERMINAL
            or attempt.status != "active"
            or attempt.lease_expires_at <= time.time()
        ):
            raise HTTPException(409, "终态不可覆盖或租约已过期")
        if job.status == "cancel_requested":
            raise HTTPException(409, "请先停止执行并报告取消")
        for artifact_id in body.artifact_ids:
            art = db.get(Artifact, artifact_id)
            if not art or art.job_id != job.id or art.attempt_id != attempt.id:
                raise HTTPException(422, "工件不属于当前执行")
        attempt.completion_hash = completion_hash
        attempt.status = "completed"
        job.status = "completed"
        job.result = body.result
        job.progress = 1
        job.error = None
        job.updated_at = time.time()
        audit(db, node.id, "job.complete", job.id)
    return view(job)


@router.post("/internal/v1/jobs/{job_id}/fail")
def fail(
    job_id: str,
    body: Failure,
    request: Request,
    node=Depends(current_node),
    db=Depends(get_db),
):
    with transaction(db):
        job, attempt = checked_attempt(
            db,
            job_id,
            body,
            node,
            request.app.state.settings,
            allow_expired=True,
            allow_terminal=True,
        )
        if job.status in {"failed", "cancelled"} and attempt.status == job.status:
            return view(job)
        if job.status in TERMINAL or not body.stopped:
            raise HTTPException(409, "须确认本次执行及其子进程已停止")
        cancelled = job.status == "cancel_requested"
        template = db.get(TaskTemplate, job.template_id)
        count = db.scalar(
            select(func.count())
            .select_from(JobAttempt)
            .where(JobAttempt.job_id == job.id)
        )
        retry = body.retryable and template.retry_safe and count < 3 and not cancelled
        attempt.status = (
            "stopped" if retry else ("cancelled" if cancelled else "failed")
        )
        job.status = "queued" if retry else attempt.status
        job.error = body.message
        job.updated_at = time.time()
        audit(db, node.id, "job.stopped", job.id)
    return view(job)


@router.post("/internal/v1/jobs/{job_id}/events")
def event(
    job_id: str,
    body: Event,
    request: Request,
    node=Depends(current_node),
    db=Depends(get_db),
):
    with transaction(db):
        job, attempt = checked_attempt(
            db, job_id, body, node, request.app.state.settings
        )
        old = db.scalar(
            select(JobEvent).where(
                JobEvent.attempt_id == attempt.id, JobEvent.sequence == body.sequence
            )
        )
        if old:
            if old.message != body.message or old.progress != body.progress:
                raise HTTPException(409, "日志序号冲突")
        else:
            db.add(
                JobEvent(
                    job_id=job.id,
                    attempt_id=attempt.id,
                    sequence=body.sequence,
                    message=body.message,
                    progress=body.progress,
                )
            )
            job.progress = max(job.progress, body.progress)
    return {"accepted": True}


@router.get("/api/v1/jobs/{job_id}/events")
def events(
    job_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:read")
    owned(db, job_id, user)
    rows = db.scalars(
        select(JobEvent)
        .where(JobEvent.job_id == job_id)
        .order_by(JobEvent.timestamp)
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [
            {
                "attempt_id": e.attempt_id,
                "sequence": e.sequence,
                "message": e.message,
                "progress": e.progress,
                "timestamp": e.timestamp,
            }
            for e in rows
        ]
    }


@router.post("/internal/v1/jobs/{job_id}/artifacts", status_code=201)
def upload(
    job_id: str,
    body: Upload,
    request: Request,
    node=Depends(current_node),
    db=Depends(get_db),
):
    settings = request.app.state.settings
    if len(body.data_base64) > ((settings.artifact_max_bytes + 2) // 3) * 4:
        raise HTTPException(413, "工件超过上传上限")
    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except ValueError:
        raise HTTPException(422, "无效 Base64")
    if (
        len(data) > settings.artifact_max_bytes
        or hashlib.sha256(data).hexdigest() != body.sha256
    ):
        raise HTTPException(422, "工件大小或校验和不匹配")
    key = uid()
    storage = LocalStorage(settings.storage_root)
    stored = False
    try:
        with transaction(db):
            job, attempt = checked_attempt(db, job_id, body, node, settings)
            previous = db.scalar(
                select(Artifact).where(
                    Artifact.attempt_id == attempt.id,
                    Artifact.sha256 == body.sha256,
                    Artifact.name == body.name,
                )
            )
            if previous:
                return {
                    "artifact_id": previous.id,
                    "sha256": previous.sha256,
                    "size": previous.size,
                }
            if (
                db.scalar(
                    select(func.count())
                    .select_from(Artifact)
                    .where(Artifact.attempt_id == attempt.id)
                )
                >= 8
            ):
                raise HTTPException(413, "单次执行工件数量超限")
            used_bytes = db.scalar(
                select(func.coalesce(func.sum(Artifact.size), 0)).where(
                    Artifact.attempt_id == attempt.id
                )
            )
            if used_bytes + len(data) > settings.artifact_max_bytes:
                raise HTTPException(413, "单次执行工件总量超限")
            size, checksum = storage.put(key, data)
            stored = True
            art = Artifact(
                job_id=job.id,
                attempt_id=attempt.id,
                owner_id=job.owner_id,
                key=key,
                name=body.name,
                size=size,
                sha256=checksum,
                expires_at=time.time() + settings.artifact_retention_days * 86400,
            )
            db.add(art)
            db.flush()
        return {"artifact_id": art.id, "sha256": art.sha256, "size": art.size}
    except BaseException:
        if stored:
            storage.delete(key)
        raise


@router.get("/api/v1/jobs/{job_id}/artifacts")
def artifacts(
    job_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "jobs:read")
    job = owned(db, job_id, user)
    rows = db.scalars(
        select(Artifact)
        .where(
            Artifact.job_id == job.id,
            Artifact.attempt_id == job.current_attempt_id,
            Artifact.expires_at > time.time(),
        )
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": a.id,
                "name": a.name,
                "size": a.size,
                "sha256": a.sha256,
                "expires_at": a.expires_at,
                "download_url": "/api/v1/artifacts/" + a.id,
            }
            for a in rows
        ]
    }


@router.get("/api/v1/artifacts/{artifact_id}")
def download(
    artifact_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    require_scope(request, "jobs:read")
    art = db.get(Artifact, artifact_id)
    if not art or art.expires_at <= time.time():
        raise HTTPException(404, "工件不存在或已过期")
    job = owned(db, art.job_id, user)
    if art.attempt_id != job.current_attempt_id:
        raise HTTPException(404, "工件属于旧执行")
    try:
        data = LocalStorage(request.app.state.settings.storage_root).get(art.key)
    except FileNotFoundError:
        raise HTTPException(410, "工件文件已不可用")
    if hashlib.sha256(data).hexdigest() != art.sha256:
        raise HTTPException(409, "工件完整性校验失败")
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{art.name}"',
            "Cache-Control": "no-store",
        },
    )


class Reconcile(Strict):
    confirmed_stopped: bool
    retry: bool = False
    evidence: str = Field(min_length=10, max_length=300)


@router.post("/api/v1/admin/jobs/{job_id}/reconcile")
def reconcile(
    job_id: str,
    body: Reconcile,
    request: Request,
    user=Depends(admin),
    db=Depends(get_db),
):
    if user.role != "owner":
        raise HTTPException(403, "只有 Owner 可人工核对失联执行")
    with transaction(db):
        sweep(db, request.app.state.settings)
        job = db.get(Job, job_id)
        if (
            not job
            or job.status not in {"reconciling", "cancel_requested"}
            or not body.confirmed_stopped
        ):
            raise HTTPException(409, "须核实原进程及子进程已停止，才能核对该任务")
        attempt = db.get(JobAttempt, job.current_attempt_id)
        template = db.get(TaskTemplate, job.template_id)
        if body.retry and not template.retry_safe:
            raise HTTPException(409, "非幂等模板禁止重试")
        cancelled = job.status == "cancel_requested"
        attempt.status = (
            "stopped"
            if body.retry and not cancelled
            else ("cancelled" if cancelled else "failed")
        )
        job.status = "queued" if attempt.status == "stopped" else attempt.status
        job.error = body.evidence
        job.updated_at = time.time()
        audit(db, user.id, "job.operator_confirmed_stopped", job.id)
    return view(job)
