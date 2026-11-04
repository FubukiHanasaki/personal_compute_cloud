"""Commit budgets, startup RAM, admission and reporting. Author: FubukiHanasaki."""

import copy
import time
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from pcc_agent.config import AgentConfig
from pcc_agent.inference import parameters
from pcc_agent.memory import resource_block_reason
from pcc_agent.services import ServiceManager
from pcc_server.jobs import eligible
from pcc_server.schemas import Capabilities
from test_services import FakeBackend
from conftest import register_node
from test_jobs import claim

WORKER = "ollama:pcc-qwen3.5-9b:q5_k_m"
REQ = {"cpu": 1, "memory_mb": 32768, "vram_mb": 1024}


def capabilities():
    return {
        "physical": {"cpu": 8, "memory_mb": 16384, "vram_mb": 8192},
        "allocatable": {"cpu": 2, "memory_mb": 8192, "vram_mb": 8192},
        "available": {"cpu": 8, "memory_mb": 8192, "vram_mb": 8192},
        "managed_commit_budget_mb": 32768,
        "managed_available_memory_mb": {WORKER: 5120},
        "workers": [WORKER],
        "isolation": ["windows-managed-job"],
        "cpu_load": 10,
        "memory_load": 40,
    }


def test_commit_limit_above_physical_capacity_remains_intact():
    caps = capabilities()
    assert Capabilities(**caps).managed_commit_budget_mb == 32768
    assert resource_block_reason(caps, WORKER, REQ) is None
    caps["available"]["memory_mb"] = 4096
    assert "启动门槛" in resource_block_reason(caps, WORKER, REQ)
    assert "启动门槛" in resource_block_reason(
        caps, WORKER, REQ, reclaimable={"memory_mb": 32768}
    )
    caps["managed_commit_budget_mb"] = 16384
    assert "提交预算" in resource_block_reason(caps, WORKER, REQ)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"available": {"cpu": 0, "memory_mb": 8192, "vram_mb": 8192}}, "CPU"),
        ({"allocatable": {"cpu": 0, "memory_mb": 8192, "vram_mb": 8192}}, "CPU"),
        ({"available": {"cpu": 8, "memory_mb": 8192, "vram_mb": 0}}, "显存"),
        ({"managed_available_memory_mb": {"ollama:another": 1024}}, "未配置"),
    ],
)
def test_resource_guards_and_worker_identity(change, reason):
    caps = {**capabilities(), **change}
    assert reason in resource_block_reason(caps, WORKER, REQ)


@pytest.mark.parametrize("value", [True, False, "5120", 5120.0, 1023, 1048577, None])
def test_threshold_strict_validation(value):
    caps = capabilities()
    caps["managed_available_memory_mb"][WORKER] = value
    with pytest.raises(ValidationError):
        Capabilities(**caps)


@pytest.mark.parametrize(
    "worker",
    [
        "compute.stats",
        "synthetic.chat",
        "ollama:",
        "http://host/model",
        "ninfer:x\n",
        "comfyui:bad key",
    ],
)
def test_threshold_key_validation(worker):
    caps = capabilities()
    caps["managed_available_memory_mb"] = {WORKER: 5120, worker: 4096}
    with pytest.raises(ValidationError):
        Capabilities(**caps)


def test_missing_settings_rejected_and_compute_stays_physical():
    caps = capabilities()
    for key in ("managed_available_memory_mb", "managed_commit_budget_mb"):
        invalid = copy.deepcopy(caps)
        invalid.pop(key)
        with pytest.raises(ValidationError):
            Capabilities(**invalid)
    caps["workers"] = ["compute.stats"]
    caps.pop("managed_available_memory_mb")
    caps.pop("managed_commit_budget_mb")
    assert Capabilities(**caps).managed_available_memory_mb == {}
    assert "额度" in resource_block_reason(caps, "compute.stats", REQ)
    invalid = capabilities()
    invalid["managed_available_memory_mb"] = {f"ollama:m{i}": 1024 for i in range(33)}
    with pytest.raises(ValidationError):
        Capabilities(**invalid)
    for budget in (True, "32768", 32768.0, -1, 1048577):
        with pytest.raises(ValidationError):
            Capabilities(**{**capabilities(), "managed_commit_budget_mb": budget})


@pytest.mark.parametrize(
    "field,value",
    [
        ("cpu_load", 85),
        ("memory_load", 90),
        ("disabled", True),
        ("last_seen", 0),
        ("mode", "maintenance"),
        ("count", 1),
    ],
)
def test_eligible_keeps_policy_load_liveness_and_concurrency_guards(field, value):
    caps = capabilities()
    policy = {
        "mode": "shared",
        "max_concurrency": 1,
        "max_cpu_load": 85,
        "max_memory_load": 90,
    }
    node = SimpleNamespace(
        id="n",
        disabled=False,
        last_seen=time.time(),
        capabilities=caps,
        policy=policy.copy(),
        local_policy=policy.copy(),
        owner_id="u",
    )
    job = SimpleNamespace(owner_id="u", requirements=REQ)
    template = SimpleNamespace(worker=WORKER)
    settings = SimpleNamespace(heartbeat_timeout=45)
    assert eligible(node, job, template, settings, {}, 0)
    count = 0
    if field in caps:
        caps[field] = value
    elif field == "mode":
        node.local_policy["mode"] = value
    elif field == "count":
        count = value
    else:
        setattr(node, field, value)
    assert not eligible(node, job, template, settings, {}, count)


def test_actual_heartbeat_claim_payload_and_queue_reason(env):
    app, c, auth, _ = env
    worker_model = WORKER.split(":", 1)[1]
    assert (
        c.post(
            "/api/v1/admin/templates",
            headers=auth,
            json={
                "id": "mem",
                "name": "memory contract",
                "kind": "ai.chat",
                "worker": WORKER,
                "model": worker_model,
                "public": True,
                "resources": REQ,
            },
        ).status_code
        == 201
    )
    identity, node, _ = register_node(c, auth, workers=[WORKER])
    caps = capabilities()
    caps["available"]["memory_mb"] = 4096
    body = {"capabilities": caps, "policy": {"mode": "shared", "max_concurrency": 2}}
    assert (
        c.put(
            f"/api/v1/nodes/{identity['node_id']}/policy",
            headers=auth,
            json={"mode": "shared", "max_concurrency": 2},
        ).status_code
        == 200
    )
    from sqlalchemy import select
    from pcc_server.models import ServiceInstance

    with app.state.sessions() as db:
        instance = db.scalar(
            select(ServiceInstance).where(
                ServiceInstance.node_id == identity["node_id"]
            )
        )
        instance.state = "idle"
        db.commit()
    assert (
        c.post("/internal/v1/nodes/heartbeat", headers=node, json=body).status_code
        == 200
    )
    job = c.post(
        "/api/v1/jobs",
        headers=auth,
        json={
            "task_type": "ai.chat",
            "template_id": "mem",
            "input": {"messages": [{"role": "user", "content": "test"}]},
        },
    )
    assert job.status_code == 202, job.text
    path = "/api/v1/jobs/" + job.json()["job_id"]
    assert "启动门槛" in c.get(path, headers=auth).json()["queue_reason"]
    assert claim(c, node).status_code == 204
    caps["available"]["memory_mb"] = 8192
    assert (
        c.post("/internal/v1/nodes/heartbeat", headers=node, json=body).status_code
        == 200
    )
    assert "等待符合条件" in c.get(path, headers=auth).json()["queue_reason"]
    lease = claim(c, node)
    assert lease.status_code == 200, lease.text
    assert lease.json()["limits"]["memory_mb"] == 32768
    assert lease.json()["template"]["config"]["resources"]["memory_mb"] == 32768
    second = c.post(
        "/api/v1/jobs",
        headers=auth,
        json={
            "task_type": "ai.chat",
            "template_id": "mem",
            "input": {"messages": [{"role": "user", "content": "another"}]},
        },
    )
    assert second.status_code == 202
    assert "提交预算不足" in second.json()["queue_reason"]
    assert claim(c, node, claim_id="y" * 32).status_code == 204
    bad = copy.deepcopy(body)
    bad["capabilities"].pop("managed_commit_budget_mb")
    assert (
        c.post("/internal/v1/nodes/heartbeat", headers=node, json=bad).status_code
        == 422
    )
    # A user job cannot supply a node's memory budget or startup gate.
    assert (
        c.post(
            "/api/v1/jobs",
            headers=auth,
            json={
                "task_type": "ai.chat",
                "template_id": "mem",
                "input": {"messages": [{"role": "user", "content": "test"}]},
                "managed_commit_budget_mb": 65536,
            },
        ).status_code
        == 422
    )


def manager(tmp_path):
    cfg = AgentConfig(
        server="https://example.com",
        name="memory-test",
        work_root=tmp_path,
        mode="shared",
        allocatable_cpu=2,
        allocatable_memory_mb=8192,
        allocatable_vram_mb=8192,
        managed_commit_budget_mb=32768,
        managed_available_memory_mb={WORKER: 5120},
    )
    result = ServiceManager(cfg, factory=FakeBackend)
    result.capabilities = capabilities()
    result.sync(
        [
            {
                "definition_id": "m",
                "config": {
                    "worker": WORKER,
                    "model": WORKER.split(":", 1)[1],
                    "resources": REQ,
                },
                "idle_seconds": 300,
                "desired_state": "ready",
            }
        ]
    )
    return result


def test_agent_enforces_new_budget_physical_gate_and_warmup(tmp_path):
    import threading

    m = manager(tmp_path)
    assert not m._exceeds_budget(m.services["m"])
    m.capabilities["available"]["memory_mb"] = 4096
    with pytest.raises(RuntimeError, match="启动门槛"):
        m.acquire("m")
    m.tick()
    assert m.services["m"].state == "stopped"
    assert m.services["m"].active_requests == 0
    m.capabilities["available"]["memory_mb"] = 8192
    event = threading.Event()
    original = m._warm

    def warm(service):
        try:
            original(service)
        finally:
            event.set()

    m._warm = warm
    m.tick()
    assert event.wait(3)
    service = m.services["m"]
    assert service.state == "idle"
    backend = service.backend
    # Reusing a loaded model does not bypass the gate.
    m.capabilities["available"]["memory_mb"] = 4096
    with pytest.raises(RuntimeError, match="启动门槛"):
        m.acquire("m")
    assert service.backend is backend
    m.config.managed_commit_budget_mb = 16384
    m.tick()
    assert backend.stopped and service.backend is None


def test_agent_missing_configuration_cannot_advertise_managed_models(tmp_path):
    with pytest.raises(ValidationError, match="预算"):
        AgentConfig(
            server="https://example.com",
            name="test",
            ollama_executable=str(tmp_path / "ollama"),
            ollama_models=["model"],
        )


@pytest.mark.parametrize("kv", ["nvfp4", "k8v4"])
def test_preserve_ninfer_kv_compatibility(kv):
    assert parameters({"kv_cache_type": kv}, "ninfer")["kv_cache_type"] == kv
    with pytest.raises(ValueError):
        parameters({"kv_cache_type": kv}, "ollama")


def test_same_model_with_changed_config_cannot_skip_startup_gate(tmp_path):
    m = manager(tmp_path)
    service = m.acquire("m")
    m.ready(service)
    m.release(service)
    backend = service.backend
    changed = {**service.config, "parameters": {"context_length": 16384}}
    m.capabilities["available"]["memory_mb"] = 4096
    with pytest.raises(RuntimeError, match="启动门槛"):
        m.acquire("m", changed)
    assert backend.stopped
    assert service.backend is None
    assert service.config["parameters"]["context_length"] == 16384
