from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from .db import get_db, transaction
from .models import Node, ServiceDefinition, ServiceInstance
from .schemas import Strict
from .security import admin, audit, can_manage

router = APIRouter()


class ServiceAction(Strict):
    desired_state: Literal["ready", "stopped", "auto"]


@router.get("/api/v1/admin/services")
def list_services(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(admin),
    db=Depends(get_db),
):
    rows = db.scalars(select(ServiceInstance).order_by(ServiceInstance.id)).all()
    rows = [i for i in rows if can_manage(user, db.get(Node, i.node_id))]
    return {
        "items": [
            {
                "id": i.id,
                "definition_id": i.definition_id,
                "node_id": i.node_id,
                "state": i.state,
                "desired_state": i.desired_state,
                "active_requests": i.active_requests,
                "last_active": i.last_active,
                "error": i.error,
                "definition": db.get(ServiceDefinition, i.definition_id).allowed_config,
            }
            for i in rows[offset : offset + limit]
        ]
    }


@router.put("/api/v1/admin/services/{instance_id}")
def service_action(
    instance_id: str, body: ServiceAction, user=Depends(admin), db=Depends(get_db)
):
    with transaction(db):
        row = db.get(ServiceInstance, instance_id)
        if not row or not can_manage(user, db.get(Node, row.node_id)):
            raise HTTPException(404, "服务不存在或无权限")
        row.desired_state = body.desired_state
        audit(db, user.id, "service.desired." + body.desired_state, row.id)
    return {"id": row.id, "desired_state": row.desired_state}
