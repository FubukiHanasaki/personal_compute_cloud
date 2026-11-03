"""Real HTTP control plane + real Agent + isolated process. Author: FubukiHanasaki."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
import httpx
import pytest
from pcc_agent.platform import isolation_capabilities, save_credential


def test_real_http_agent_compute_and_artifact(env, tmp_path):
    if sys.platform != "linux" or not isolation_capabilities():
        pytest.skip("Linux nonprivileged bubblewrap is required")
    app, _, auth, _ = env
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server_env = {
        **os.environ,
        "PCC_DATABASE_URL": app.state.settings.database_url,
        "PCC_SECRET": app.state.settings.secret,
        "PCC_ENVIRONMENT": "development",
        "PCC_SECURE_COOKIES": "false",
        "PCC_STORAGE_ROOT": str(tmp_path / "storage"),
        "PCC_OWNER_PASSWORD": "",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    logfile = (tmp_path / "server.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pcc_server.app:create_app",
            "--factory",
            "--fd",
            str(listener.fileno()),
        ],
        env=server_env,
        stdout=logfile,
        stderr=logfile,
        pass_fds=(listener.fileno(),),
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
        ) as client:
            until = time.monotonic() + 10
            while True:
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > until:
                    raise AssertionError("HTTP server did not become healthy")
                time.sleep(0.1)
            assert (
                client.post(
                    "/api/v1/admin/templates",
                    headers=auth,
                    json={
                        "id": "network-stats",
                        "name": "网络闭环",
                        "kind": "compute.stats",
                        "worker": "compute.stats",
                        "public": True,
                    },
                ).status_code
                == 201
            )
            registration = client.post(
                "/api/v1/admin/registration-tokens", headers=auth, json={}
            ).json()["token"]
            identity = client.post(
                "/internal/v1/nodes/register",
                json={
                    "token": registration,
                    "name": "real-outbound-agent",
                    "os": "Linux",
                },
            ).json()
            credential = tmp_path / "credential.json"
            save_credential(credential, identity)
            config = tmp_path / "agent.toml"
            config.write_text(
                f'version=1\nserver="http://127.0.0.1:{port}"\nname="real-outbound-agent"\ncredential_file={json.dumps(str(credential))}\nwork_root={json.dumps(str(tmp_path / "agent"))}\nmode="shared"\nallow_development_http=true\nenable_compute=true\nmax_cpu_load=100\nmax_memory_load=100\n',
                encoding="utf-8",
            )
            from pcc_agent.runtime import Agent
            from pcc_agent.execution import execute

            agent = Agent(config)
            try:
                agent.heartbeat()
                client.put(
                    f"/api/v1/nodes/{identity['node_id']}/policy",
                    headers=auth,
                    json={
                        "mode": "shared",
                        "max_cpu_load": 100,
                        "max_memory_load": 100,
                    },
                )
                submitted = client.post(
                    "/api/v1/jobs",
                    headers=auth,
                    json={
                        "task_type": "compute.stats",
                        "template_id": "network-stats",
                        "input": {"values": [2, 4, 6]},
                    },
                )
                assert submitted.status_code == 202
                jid = submitted.json()["job_id"]
                attempt = agent.client.post(
                    "/internal/v1/jobs/claim", json={"claim_id": uuid.uuid4().hex}
                )
                assert attempt.status_code == 200, attempt.text
                execute(agent, attempt.json())
                job = client.get(f"/api/v1/jobs/{jid}", headers=auth).json()
                assert job["status"] == "completed", job
                assert job["result"]["sum"] == 12
                artifact = client.get(
                    f"/api/v1/jobs/{jid}/artifacts", headers=auth
                ).json()["items"][0]
                assert (
                    client.get(artifact["download_url"], headers=auth).json()["mean"]
                    == 4
                )
            finally:
                agent.services.stop_all()
                agent.client.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
        listener.close()
        logfile.close()
