import time
from typing import Literal
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field
from sqlalchemy import delete, select
from .db import get_db, transaction
from .models import APIKey, AuditEvent, LoginBucket, User, WebSession
from .schemas import Strict
from .security import SCOPES, admin, audit, current_user, digest, hash_password, token, verify_password

router = APIRouter()
DUMMY_HASH = hash_password("unused-timing-equalization-value")


class Login(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class NewUser(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=16, max_length=256)
    role: Literal["admin", "user"] = "user"


class Grants(Strict):
    nodes: list[str] = Field(default_factory=list, max_length=100)
    view_nodes: list[str] = Field(default_factory=list, max_length=100)
    templates: list[str] = Field(default_factory=list, max_length=100)
    jobs: list[str] = Field(default_factory=list, max_length=100)


class UserUpdate(Strict):
    status: Literal["active", "disabled"] = "active"
    grants: Grants = Grants()


class KeyCreate(Strict):
    scopes: list[str] = Field(min_length=1, max_length=6)
    expires_seconds: int = Field(default=86400, ge=60, le=90*86400)


class PasswordChange(Strict):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=16, max_length=256)


def user_view(user):
    return {"id": user.id, "username": user.username, "role": user.role, "status": user.status, "grants": user.grants}


def owner_only(user):
    if user.role != "owner":
        raise HTTPException(403, "此操作需要 Owner 权限")


@router.post("/api/v1/auth/login")
def login(body: Login, request: Request, response: Response, db=Depends(get_db)):
    settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "拒绝跨站登录")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "拒绝跨站登录")
    bucket_key = digest("login:"+(request.client.host if request.client else "unknown"), settings)
    raw = token("session_")
    allowed = False
    limited = False
    with transaction(db):
        now = time.time()
        db.execute(delete(LoginBucket).where(LoginBucket.resets_at<now))
        bucket = db.get(LoginBucket, bucket_key)
        if not bucket:
            bucket = LoginBucket(id=bucket_key, attempts=0, resets_at=now+900)
            db.add(bucket)
        if bucket.attempts>=10:
            limited = True
        else:
            bucket.attempts += 1
            user = db.scalar(select(User).where(User.username == body.username))
            valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
            allowed = valid and user and user.status == "active"
            if allowed:
                db.add(WebSession(user_id=user.id, hash=digest(raw, settings), expires_at=now+settings.session_hours*3600))
                bucket.attempts = 0
                audit(db, user.id, "auth.login", user.id)
            else:
                audit(db, "anonymous", "auth.login", "account", "denied")
    if limited:
        raise HTTPException(429, "登录尝试过多，请稍后重试")
    if not allowed:
        raise HTTPException(401, "用户名或密码错误")
    response.set_cookie("pcc_session", raw, httponly=True, secure=settings.secure_cookies, samesite="strict", max_age=settings.session_hours*3600, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_view(user), "csrf_token": digest("csrf:"+raw, settings)}


@router.get("/api/v1/auth/me")
def me(request: Request, user=Depends(current_user)):
    raw = request.cookies.get("pcc_session", "")
    return {"user": user_view(user), "csrf_token": digest("csrf:"+raw, request.app.state.settings) if raw else None, "scopes": request.state.scopes}


@router.post("/api/v1/auth/logout", status_code=204)
def logout(request: Request, response: Response, user=Depends(current_user), db=Depends(get_db)):
    with transaction(db):
        session_id = getattr(request.state, "session_id", None)
        if session_id:
            db.execute(delete(WebSession).where(WebSession.id == session_id))
        audit(db, user.id, "auth.logout", user.id)
    response.delete_cookie("pcc_session", path="/")


@router.post("/api/v1/auth/password", status_code=204)
def change_password(body: PasswordChange, user=Depends(current_user), db=Depends(get_db)):
    with transaction(db):
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(403, "当前密码错误")
        user.password_hash = hash_password(body.new_password)
        db.execute(delete(WebSession).where(WebSession.user_id == user.id))
        audit(db, user.id, "auth.password", user.id)


@router.post("/api/v1/keys", status_code=201)
def create_key(body: KeyCreate, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if not set(body.scopes)<=SCOPES or ("admin" in body.scopes and user.role not in {"owner", "admin"}):
        raise HTTPException(403, "无权签发此作用域")
    if not set(body.scopes)<=set(request.state.scopes) and "admin" not in request.state.scopes:
        raise HTTPException(403, "不能扩大当前凭证权限")
    # A read-only key must not be able to mint an admin or write key.
    if "admin" not in request.state.scopes and "jobs:write" not in request.state.scopes:
        raise HTTPException(403, "当前凭证不能创建密钥")
    raw = token("pcc_")
    with transaction(db):
        key = APIKey(user_id=user.id, hash=digest(raw, request.app.state.settings), scopes=sorted(set(body.scopes)), expires_at=time.time()+body.expires_seconds)
        db.add(key)
        db.flush()
        audit(db, user.id, "key.create", key.id)
    return {"id": key.id, "key": raw, "scopes": key.scopes, "expires_at": key.expires_at}


@router.get("/api/v1/keys")
def keys(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(current_user), db=Depends(get_db)):
    rows = db.scalars(select(APIKey).where(APIKey.user_id == user.id).offset(offset).limit(limit))
    return {"items": [{"id": k.id, "scopes": k.scopes, "expires_at": k.expires_at, "revoked_at": k.revoked_at} for k in rows]}


@router.post("/api/v1/keys/{key_id}/revoke", status_code=204)
def revoke_key(key_id: str, user=Depends(current_user), db=Depends(get_db)):
    with transaction(db):
        key = db.get(APIKey, key_id)
        if not key or (key.user_id != user.id and user.role != "owner"):
            raise HTTPException(404, "密钥不存在或无权限")
        key.revoked_at = time.time()
        audit(db, user.id, "key.revoke", key.id)


@router.post("/api/v1/admin/users", status_code=201)
def create_user(body: NewUser, user=Depends(admin), db=Depends(get_db)):
    owner_only(user)
    with transaction(db):
        if db.scalar(select(User).where(User.username == body.username)):
            raise HTTPException(409, "用户名已存在")
        row = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
        db.add(row)
        db.flush()
        audit(db, user.id, "user.create", row.id)
    return user_view(row)


@router.get("/api/v1/admin/users")
def users(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(admin), db=Depends(get_db)):
    owner_only(user)
    return {"items": [user_view(u) for u in db.scalars(select(User).order_by(User.username).offset(offset).limit(limit))]}


@router.put("/api/v1/admin/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, user=Depends(admin), db=Depends(get_db)):
    owner_only(user)
    with transaction(db):
        row = db.get(User, user_id)
        if not row or row.role == "owner":
            raise HTTPException(403, "不能修改 Owner 或不存在的用户")
        row.status = body.status
        row.grants = body.grants.model_dump()
        if row.status == "disabled":
            db.execute(delete(WebSession).where(WebSession.user_id == row.id))
        audit(db, user.id, "user.update", row.id)
    return user_view(row)


@router.get("/api/v1/admin/audit")
def audit_events(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(admin), db=Depends(get_db)):
    owner_only(user)
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.timestamp.desc()).offset(offset).limit(limit))
    return {"items": [{"id": a.id, "actor": a.actor, "action": a.action, "target": a.target, "timestamp": a.timestamp, "outcome": a.outcome} for a in rows]}
