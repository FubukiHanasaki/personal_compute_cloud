import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.platform import discover


def test_https_and_fail_closed_defaults(tmp_path):
    config = AgentConfig(
        server="https://example.com", name="any-name", work_root=tmp_path
    )
    assert config.mode == "maintenance"
    assert not config.enable_compute
    with pytest.raises(ValueError):
        AgentConfig(
            server="http://remote.example", name="node", allow_development_http=True
        )
    with pytest.raises(ValueError):
        AgentConfig(server="https://user:password@example.com", name="node")
    with pytest.raises(ValueError):
        AgentConfig(server="https://example.com", name="node", version=2)
    assert AgentConfig(
        server="http://127.0.0.1:8000", name="node", allow_development_http=True
    )


def test_platform_discovery_never_silently_downgrades_isolation(monkeypatch, tmp_path):
    import pcc_agent.platform as platform_adapter

    monkeypatch.setattr(platform_adapter, "gpu_metrics", lambda: [])
    config = AgentConfig(
        server="https://example.com",
        name="arbitrary-hardware",
        work_root=tmp_path,
        enable_compute=True,
        enable_synthetic_ai=True,
    )
    report = discover(config, isolation=[])
    assert report["workers"] == []
    for resource in ("cpu", "memory_mb", "vram_mb"):
        assert report["allocatable"][resource] <= report["physical"][resource]
