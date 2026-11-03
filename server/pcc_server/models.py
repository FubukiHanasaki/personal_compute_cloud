import time
import uuid
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def uid():
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(16), default="user")
    status: Mapped[str] = mapped_column(String(16), default="active")
    grants: Mapped[dict] = mapped_column(JSON, default=dict)


class APIKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[float] = mapped_column(Float)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class RegistrationToken(Base):
    __tablename__ = "registration_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[float] = mapped_column(Float)
    used_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    issued_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80))
    os: Mapped[str] = mapped_column(String(32))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    policy: Mapped[dict] = mapped_column(JSON, default=lambda: {"mode": "maintenance"})
    local_policy: Mapped[dict] = mapped_column(JSON, default=lambda: {"mode": "maintenance"})
    last_seen: Mapped[float] = mapped_column(Float, default=time.time)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class NodeCredential(Base):
    __tablename__ = "node_credentials"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[float] = mapped_column(Float)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(80))
    timestamp: Mapped[float] = mapped_column(Float, default=time.time)
    outcome: Mapped[str] = mapped_column(String(40), default="success")


class TaskTemplate(Base):
    __tablename__ = "task_templates"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    worker: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    public: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_safe: Mapped[bool] = mapped_column(Boolean, default=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("task_templates.id"))
    task_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    input: Mapped[dict] = mapped_column(JSON)
    requirements: Mapped[dict] = mapped_column(JSON)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    current_attempt_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("node_id", "claim_id"),
        Index("uq_job_live_attempt", "job_id", unique=True,
              postgresql_where=text("status IN ('active', 'reconciling')"),
              sqlite_where=text("status IN ('active', 'reconciling')")),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    claim_id: Mapped[str] = mapped_column(String(64))
    lease_token_hash: Mapped[str] = mapped_column(String(64))
    lease_expires_at: Mapped[float] = mapped_column(Float)
    deadline: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="active")
    completion_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (UniqueConstraint("attempt_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    attempt_id: Mapped[str] = mapped_column(ForeignKey("job_attempts.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String(4096))
    progress: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[float] = mapped_column(Float, default=time.time)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("job_attempts.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)
