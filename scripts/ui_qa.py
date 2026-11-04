"""Disposable real HTTP UI environment. Author: FubukiHanasaki."""

import base64, json, secrets, time, uuid, threading
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "tmp/ui-qa"
root.mkdir(parents=True, exist_ok=True)
credentials = {
    "username": "FubukiHanasaki",
    "password": secrets.token_urlsafe(24),
    "viewer_password": secrets.token_urlsafe(24),
}
file = root / "credentials.json"
file.write_text(json.dumps(credentials))
file.chmod(0o600)
from pcc_server.app import create_app
from pcc_server.config import Settings
from pcc_server.db import Base
from pcc_server.models import (
    User,
    TaskTemplate,
    Job,
    Node,
    ServiceDefinition,
    ServiceInstance,
    JobAttempt,
    Artifact,
)
from pcc_server.security import hash_password
from pcc_agent.gui import gui_server

settings = Settings(
    _env_file=None,
    environment="development",
    secret=secrets.token_hex(32),
    secure_cookies=False,
    database_url=f"sqlite:///{root / 'qa.sqlite'}",
    storage_root=root / "artifacts",
    web_root=Path(__file__).resolve().parents[1] / "tmp/web-dist",
)
app = create_app(settings)
Base.metadata.drop_all(app.state.engine)
Base.metadata.create_all(app.state.engine)
with app.state.sessions() as db:
    owner = User(
        username=credentials["username"],
        password_hash=hash_password(credentials["password"]),
        role="owner",
    )
    viewer = User(
        username="viewer",
        password_hash=hash_password(credentials["viewer_password"]),
        role="user",
        grants={},
    )
    db.add_all([owner, viewer])
    db.flush()
    for id, name, kind, worker, model in [
        ("stats", "数字统计", "compute.stats", "compute.stats", None),
        ("chat", "本地对话", "ai.chat", "ollama:qwen3:8b", "qwen3:8b"),
        ("image", "图片模型", "ai.image", "comfyui:local-image", "local-image"),
    ]:
        db.add(
            TaskTemplate(
                id=id,
                name=name,
                kind=kind,
                worker=worker,
                public=id == "stats",
                retry_safe=False,
                config={
                    "resources": {"cpu": 2, "memory_mb": 2048, "vram_mb": 0},
                    "max_timeout_seconds": 120,
                    "model": model,
                    "parameters": {
                        "vision": True,
                        "context_length": 8192,
                        "max_tokens": 2048,
                    },
                },
            )
        )
    node = Node(
        name="Windows 工作站",
        os="Windows",
        owner_id=owner.id,
        last_seen=time.time(),
        policy={"mode": "shared"},
        local_policy={"mode": "shared"},
        capabilities={
            "physical": {"cpu": 16, "memory_mb": 32768, "vram_mb": 12288},
            "allocatable": {"cpu": 4, "memory_mb": 8192, "vram_mb": 8192},
            "available": {"cpu": 16, "memory_mb": 22000, "vram_mb": 10000},
            "cpu_load": 24,
            "memory_load": 33,
            "workers": ["ollama:qwen3:8b", "comfyui:local-image"],
        },
    )
    db.add(node)
    db.flush()
    definition = ServiceDefinition(
        id="chat",
        type="ollama",
        allowed_config={
            "worker": "ollama:qwen3:8b",
            "model": "qwen3:8b",
            "resources": {"cpu": 2, "memory_mb": 2048, "vram_mb": 0},
        },
        idle_policy={"idle_seconds": 300},
    )
    db.add(definition)
    db.flush()
    db.add(
        ServiceInstance(
            definition_id="chat",
            node_id=node.id,
            state="ready",
            desired_state="auto",
            active_requests=0,
        )
    )
    for i, status in enumerate(
        ["queued", "running", "completed", "failed", "reconciling"]
    ):
        db.add(
            Job(
                owner_id=owner.id,
                template_id="stats",
                task_type="compute.stats",
                input={"values": [1, 2, 3]},
                requirements={"cpu": 1, "memory_mb": 256, "vram_mb": 0},
                request_hash="qa",
                timeout_seconds=120,
                status=status,
                progress=[0, 0.42, 1, 0, 0.68][i],
                result={"sum": 6} if status == "completed" else None,
            )
        )
    image_job = Job(
        owner_id=owner.id,
        template_id="image",
        task_type="ai.image",
        input={"prompt": "fixture", "size": "512x512"},
        requirements={"cpu": 1, "memory_mb": 256, "vram_mb": 0},
        request_hash="qa-image",
        timeout_seconds=120,
        status="completed",
        progress=1,
        result={"model": "local-image"},
    )
    db.add(image_job)
    db.flush()
    attempt = JobAttempt(
        job_id=image_job.id,
        node_id=node.id,
        claim_id=uuid.uuid4().hex,
        lease_token_hash="fixture-only",
        lease_expires_at=time.time() + 60,
        deadline=time.time() + 120,
        status="completed",
    )
    db.add(attempt)
    db.flush()
    image_job.current_attempt_id = attempt.id
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII="
    )
    key = uuid.uuid4().hex
    size, sha = app.state.storage.put(key, png)
    db.add(
        Artifact(
            job_id=image_job.id,
            attempt_id=attempt.id,
            owner_id=owner.id,
            key=key,
            name="fixture.png",
            size=size,
            sha256=sha,
            expires_at=time.time() + 3600,
        )
    )
    db.commit()


# Browser fixtures exercise persistence through real HTTP, without model weights.
def finish_browser_chat_fixtures():
    from sqlalchemy import select

    while True:
        time.sleep(0.5)
        with app.state.sessions() as db:
            for job in db.scalars(
                select(Job).where(Job.task_type == "ai.chat", Job.status == "queued")
            ):
                if job.input["messages"][-1]["content"] == "模拟一次失败（界面验收）" and not any(
                    earlier.input["messages"] == job.input["messages"]
                    for earlier in db.scalars(select(Job).where(Job.task_type == "ai.chat", Job.status == "failed"))
                ):
                    job.status = "failed"
                    job.error = "界面验收：模拟临时推理失败"
                    continue
                job.status = "completed"
                job.progress = 1
                job.result = {
                    "content": "界面验收回复（合成夹具，非真实模型）：消息已保存。"
                }
            db.commit()


threading.Thread(target=finish_browser_chat_fixtures, daemon=True).start()
import uvicorn

with gui_server(root / "agent.toml", 8891) as gui:
    (root / "gui-url.json").write_text(json.dumps({"url": gui.url}))
    (root / "gui-url.json").chmod(0o600)
    uvicorn.run(app, host="127.0.0.1", port=8890, log_level="warning")
