import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from .db import get_db, transaction
from .models import (
    Node,
    NodeCredential,
    RegistrationToken,
    ServiceDefinition,
    ServiceInstance,
    User,
)
from .schemas import Heartbeat, IssueToken, Policy, Register
from .security import (
    admin,
    audit,
    can_manage,
    current_node,
    current_user,
    digest,
    require_scope,
    token,
)

router = APIRouter()


def node_view(node, settings):
    age = time.time() - node.last_seen
    status = (
        "disabled"
        if node.disabled
        else (
            "online"
            if age <= settings.heartbeat_timeout
            else ("stale" if age <= settings.heartbeat_timeout * 3 else "offline")
        )
    )
    return {
        "id": node.id,
        "name": node.name,
        "os": node.os,
        "status": status,
        "capabilities": node.capabilities,
        "policy": node.policy,
        "local_policy": node.local_policy,
        "last_seen": node.last_seen,
        "disabled": node.disabled,
    }


@router.post("/api/v1/admin/registration-tokens", status_code=201)
def issue_token(
    body: IssueToken, request: Request, user=Depends(admin), db=Depends(get_db)
):
    raw = token("reg_")
    with transaction(db):
        user = admin(request, current_user(request, db))
        row = RegistrationToken(
            hash=digest(raw, request.app.state.settings),
            expires_at=time.time() + body.expires_seconds,
            issued_by=user.id,
        )
        db.add(row)
        db.flush()
        audit(db, user.id, "registration.issue", row.id)
    return {"token": raw, "expires_at": row.expires_at}


@router.post("/internal/v1/nodes/register", status_code=201)
def register(body: Register, request: Request, db=Depends(get_db)):
    settings = request.app.state.settings
    raw = token("node_")
    with transaction(db):
        row = db.scalar(
            select(RegistrationToken).where(
                RegistrationToken.hash == digest(body.token, settings)
            )
        )
        if not row or row.used_at or row.expires_at <= time.time():
            raise HTTPException(401, "注册令牌已过期或已使用")
        issuer = db.get(User, row.issued_by)
        if (
            not issuer
            or issuer.status != "active"
            or issuer.role not in {"owner", "admin"}
        ):
            raise HTTPException(401, "注册令牌签发者已失去授权")
        row.used_at = time.time()
        owner = db.scalar(select(User).where(User.role == "owner"))
        if not owner:
            raise HTTPException(409, "请先初始化 Owner")
        node = Node(name=body.name, os=body.os, owner_id=owner.id)
        db.add(node)
        db.flush()
        db.add(
            NodeCredential(
                node_id=node.id,
                hash=digest(raw, settings),
                expires_at=time.time() + 365 * 86400,
            )
        )
        audit(db, node.id, "node.register", node.id)
    return {
        "node_id": node.id,
        "owner_id": node.owner_id,
        "credential": raw,
        "expires_in": 365 * 86400,
    }


@router.post("/internal/v1/nodes/heartbeat")
def heartbeat(
    body: Heartbeat, request: Request, node=Depends(current_node), db=Depends(get_db)
):
    with transaction(db):
        caps = body.capabilities.model_dump()
        for key in ("cpu", "memory_mb", "vram_mb"):
            if (
                caps["allocatable"][key] > caps["physical"][key]
                or caps["available"][key] > caps["physical"][key]
            ):
                raise HTTPException(422, "资源报告超过物理容量")
        node.capabilities = caps
        node.local_policy = body.policy.model_dump()
        node.last_seen = time.time()
        definitions = db.scalars(select(ServiceDefinition)).all()
        for definition in definitions:
            instance = db.scalar(
                select(ServiceInstance).where(
                    ServiceInstance.definition_id == definition.id,
                    ServiceInstance.node_id == node.id,
                )
            )
            if not instance:
                if definition.allowed_config["worker"] not in caps["workers"]:
                    continue
                instance = ServiceInstance(definition_id=definition.id, node_id=node.id)
                db.add(instance)
                db.flush()
            report = next(
                (r for r in body.services if r.definition_id == definition.id), None
            )
            if report:
                if instance.state != report.state:
                    audit(
                        db,
                        node.id,
                        "service." + report.state,
                        instance.id,
                        "error" if report.state == "error" else "success",
                    )
                instance.state = report.state
                instance.active_requests = report.active_requests
                instance.last_active = min(report.last_active, time.time())
                instance.error = report.error
        commands = [
            {
                "definition_id": i.definition_id,
                "desired_state": i.desired_state
                if db.get(ServiceDefinition, i.definition_id).allowed_config["worker"]
                in caps["workers"]
                else "stopped",
                "config": db.get(ServiceDefinition, i.definition_id).allowed_config,
                "idle_seconds": db.get(ServiceDefinition, i.definition_id).idle_policy[
                    "idle_seconds"
                ],
            }
            for i in db.scalars(
                select(ServiceInstance).where(ServiceInstance.node_id == node.id)
            )
        ]
    return {
        "node_id": node.id,
        "owner_id": node.owner_id,
        "policy": node.policy,
        "server_time": time.time(),
        "services": commands,
    }


@router.get("/api/v1/nodes")
def nodes(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db=Depends(get_db),
):
    require_scope(request, "nodes:read")
    rows = db.scalars(select(Node).order_by(Node.id)).all()
    rows = [
        n
        for n in rows
        if can_manage(user, n) or n.id in user.grants.get("view_nodes", [])
    ]
    return {
        "items": [
            node_view(n, request.app.state.settings)
            for n in rows[offset : offset + limit]
        ],
        "offset": offset,
        "limit": limit,
    }


@router.put("/api/v1/nodes/{node_id}/policy")
def update_policy(node_id: str, body: Policy, user=Depends(admin), db=Depends(get_db)):
    with transaction(db):
        node = db.get(Node, node_id)
        if not node or not can_manage(user, node):
            raise HTTPException(404, "节点不存在或无权限")
        node.policy = body.model_dump()
        audit(db, user.id, "node.policy", node.id)
    return {"id": node.id, "policy": node.policy}


@router.post("/api/v1/nodes/{node_id}/revoke")
def revoke_node(node_id: str, user=Depends(admin), db=Depends(get_db)):
    with transaction(db):
        node = db.get(Node, node_id)
        if not node or not can_manage(user, node):
            raise HTTPException(404, "节点不存在或无权限")
        node.disabled = True
        for cred in db.scalars(
            select(NodeCredential).where(NodeCredential.node_id == node.id)
        ):
            cred.revoked_at = time.time()
        audit(db, user.id, "node.revoke", node.id)
    return {"id": node.id, "status": "disabled"}
