import json
from pathlib import Path
import pytest
from pcc_agent.config import AgentConfig
from pcc_agent.platform import isolation_capabilities
from pcc_agent.sandbox import SandboxedWorker


def test_real_isolated_compute(tmp_path):
    if not isolation_capabilities():
        pytest.skip("Host does not permit nonprivileged bubblewrap")
    config = AgentConfig(server="https://example.com", name="test", work_root=tmp_path)
    payload = {"template": {"worker": "compute.stats"}, "input": {"values": [1, 2, 3]}, "limits": {"cpu": 1, "memory_mb": 256, "timeout_seconds": 10, "output_bytes": 1024*1024}}
    worker = SandboxedWorker(config, payload)
    try:
        worker.process.wait(timeout=15)
        assert worker.result()["result"]["sum"] == 6
    finally:
        worker.close()


def test_submission_to_real_worker_to_download(env, tmp_path):
    if not isolation_capabilities():
        pytest.skip("Host does not permit nonprivileged bubblewrap")
    from types import SimpleNamespace
    from conftest import register_node
    from test_jobs import template, submit, claim
    from pcc_agent.execution import execute
    app, client, auth, _ = env
    template(client, auth)
    _, node_auth, _ = register_node(client, auth)
    jid = submit(client, auth).json()["job_id"]
    payload = claim(client, node_auth).json()
    class Transport:
        def post(self, path, json):
            return client.post(path, json=json, headers=node_auth)
    config = AgentConfig(server="https://example.com", name="test", work_root=tmp_path, mode="shared")
    agent = SimpleNamespace(config=config, client=Transport(), heartbeat=lambda: None)
    execute(agent, payload)
    status = client.get(f"/api/v1/jobs/{jid}", headers=auth).json()
    assert status["status"] == "completed", status
    assert status["result"]["sum"] == 6
    artifact = client.get(f"/api/v1/jobs/{jid}/artifacts", headers=auth).json()["items"][0]
    assert client.get(artifact["download_url"], headers=auth).json()["mean"] == 2
