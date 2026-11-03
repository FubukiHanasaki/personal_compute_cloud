import hashlib
import hmac
import secrets
import time
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from .db import get_db
from .models import APIKey, AuditEvent, Node, NodeCredential, User

SCOPES = {"jobs:read", "jobs:write", "models:read", "nodes:read", "admin"}


def token(prefix):
    return prefix + secrets.token_urlsafe(32)


def digest(value, settings):
    return hmac.new(settings.secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def hash_password(password):
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + key.hex()


def verify_password(password, stored):
    try:
        salt, expected = stored.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def audit(db, actor, action, target, outcome="success"):
    db.add(AuditEvent(actor=actor, action=action, target=target, outcome=outcome))


def bearer(request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "需要身份凭证")
    return auth[7:]


def current_user(request: Request, db=Depends(get_db)):
    raw = bearer(request)
    key = db.scalar(select(APIKey).where(APIKey.hash == digest(raw, request.app.state.settings)))
    if not key or key.revoked_at or key.expires_at <= time.time():
        raise HTTPException(401, "凭证无效或已过期")
    user = db.get(User, key.user_id)
    if not user or user.status != "active":
        raise HTTPException(401, "账户不可用")
    request.state.scopes = key.scopes
    return user


def require_scope(request, scope):
    scopes = request.state.scopes
    if scope not in scopes and "admin" not in scopes:
        raise HTTPException(403, "凭证作用域不足")


def admin(request: Request, user=Depends(current_user)):
    require_scope(request, "admin")
    if user.role not in {"owner", "admin"}:
        raise HTTPException(403, "需要管理员权限")
    return user


def can_manage(user, node):
    return user.role == "owner" or (user.role == "admin" and node.id in user.grants.get("nodes", []))


def current_node(request: Request, db=Depends(get_db)):
    raw = bearer(request)
    credential = db.scalar(select(NodeCredential).where(NodeCredential.hash == digest(raw, request.app.state.settings)))
    if not credential or credential.revoked_at or credential.expires_at <= time.time():
        raise HTTPException(401, "节点凭证无效或已过期")
    node = db.get(Node, credential.node_id)
    if not node or node.disabled:
        raise HTTPException(403, "节点已禁用")
    return node
