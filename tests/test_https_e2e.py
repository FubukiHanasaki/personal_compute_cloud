"""Verified TLS proxy -> Server -> outbound Agent -> artifact. Author: Fubuki."""

import json
import os
import socket
import ssl
import subprocess
import sys
import time
import uuid
import httpx
import pytest


def test_verified_https_agent_compute_and_persistent_restart(env, tmp_path):
    from pcc_agent.platform import isolation_capabilities, save_credential

    if not os.environ.get("PCC_CADDY_BIN") or not isolation_capabilities():
        pytest.skip("Requires Caddy and Linux isolation")
    from pcc_agent.runtime import Agent
    from pcc_agent.execution import execute

    app, _, auth, _ = env
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        tls_port = reserve.getsockname()[1]
    log = (tmp_path / "http.log").open("w")
    tls_log = (tmp_path / "tls.log").open("w")
    server_env = {
        **os.environ,
        "PCC_DATABASE_URL": app.state.settings.database_url,
        "PCC_SECRET": app.state.settings.secret,
        "PCC_ENVIRONMENT": "development",
        "PCC_SECURE_COOKIES": "true",
        "PCC_STORAGE_ROOT": str(tmp_path / "storage"),
        "PCC_OWNER_PASSWORD": "",
    }

    def start():
        return subprocess.Popen(
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
            stdout=log,
            stderr=log,
            pass_fds=(listener.fileno(),),
        )

    server = start()
    proxy = agent = None
    try:
        config = tmp_path / "Caddyfile"
        config.write_text(f"""{{
    admin off
    auto_https disable_redirects
    skip_install_trust
}}
https://localhost:{tls_port} {{
    bind 127.0.0.1
    tls internal
    reverse_proxy 127.0.0.1:{port}
}}
""")
        proxy = subprocess.Popen(
            [os.environ["PCC_CADDY_BIN"], "run", "--config", str(config)],
            env={
                **os.environ,
                "XDG_DATA_HOME": str(tmp_path / "caddy-data"),
                "XDG_CONFIG_HOME": str(tmp_path / "caddy-config"),
            },
            stdout=tls_log,
            stderr=tls_log,
        )
        ca = tmp_path / "caddy-data/caddy/pki/authorities/local/root.crt"
        until = time.monotonic() + 15
        while not ca.exists():
            assert proxy.poll() is None, (tmp_path / "tls.log").read_text()
            assert time.monotonic() < until
            time.sleep(0.1)
        origin = f"https://localhost:{tls_port}"
        with httpx.Client(
            base_url=origin,
            verify=ssl.create_default_context(cafile=str(ca)),
            trust_env=False,
            timeout=10,
        ) as client:
            while True:
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < until
                time.sleep(0.1)
            # Public trust alone must reject our private test CA; no verify=False.
            with pytest.raises(httpx.ConnectError):
                httpx.get(origin + "/healthz", trust_env=False)
            response = client.post(
                "/api/v1/auth/login",
                json={"username": "Fubuki", "password": "test-password-0123456"},
            )
            assert response.status_code == 200
            assert (
                "Secure" in response.headers["set-cookie"]
                and "HttpOnly" in response.headers["set-cookie"]
            )
            csrf = {"X-CSRF-Token": response.json()["csrf_token"]}
            key = client.post(
                "/api/v1/keys",
                headers=csrf,
                json={
                    "scopes": ["jobs:read", "jobs:write", "models:read"],
                    "expires_seconds": 3600,
                },
            ).json()["key"]
            assert (
                client.post(
                    "/api/v1/admin/templates",
                    headers=auth,
                    json={
                        "id": "tls-stats",
                        "name": "TLS 统计",
                        "kind": "compute.stats",
                        "worker": "compute.stats",
                        "public": True,
                    },
                ).status_code
                == 201
            )
            token = client.post(
                "/api/v1/admin/registration-tokens", headers=auth, json={}
            ).json()["token"]
            identity = client.post(
                "/internal/v1/nodes/register",
                json={"token": token, "name": "verified-tls-node", "os": "Linux"},
            ).json()
            save_credential(tmp_path / "identity.json", identity)
            agent_config = tmp_path / "agent.toml"
            agent_config.write_text(
                f'version=1\nserver={json.dumps(origin)}\nname="verified-tls-node"\nca_file={json.dumps(str(ca))}\ncredential_file="identity.json"\nwork_root="worker"\nmode="shared"\nenable_compute=true\nmax_cpu_load=100\nmax_memory_load=100\n'
            )
            agent = Agent(agent_config)
            agent.heartbeat()
            assert (
                client.put(
                    f"/api/v1/nodes/{identity['node_id']}/policy",
                    headers=auth,
                    json={
                        "mode": "shared",
                        "max_cpu_load": 100,
                        "max_memory_load": 100,
                    },
                ).status_code
                == 200
            )
            bearer = {"Authorization": "Bearer " + key}
            submitted = client.post(
                "/api/v1/jobs",
                headers=bearer,
                json={
                    "template_id": "tls-stats",
                    "task_type": "compute.stats",
                    "input": {"values": [3, 6, 9]},
                },
            )
            assert submitted.status_code == 202
            job_id = submitted.json()["job_id"]
            attempt = agent.client.post(
                "/internal/v1/jobs/claim", json={"claim_id": uuid.uuid4().hex}
            )
            assert attempt.status_code == 200
            execute(agent, attempt.json())
            assert (
                client.get(f"/api/v1/jobs/{job_id}", headers=bearer).json()["result"][
                    "sum"
                ]
                == 18
            )
            file = client.get(
                f"/api/v1/jobs/{job_id}/artifacts", headers=bearer
            ).json()["items"][0]
            assert client.get(file["download_url"], headers=bearer).json()["sum"] == 18
            server.terminate()
            server.wait(timeout=10)
            server = start()
            until = time.monotonic() + 10
            while True:
                try:
                    response = client.get(f"/api/v1/jobs/{job_id}", headers=bearer)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < until
                time.sleep(0.1)
            assert response.json()["status"] == "completed"
            assert client.get(file["download_url"], headers=bearer).json()["sum"] == 18
            agent.heartbeat()
    finally:
        if agent:
            agent.services.stop_all()
            agent.client.close()
        for process in (server, proxy):
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        listener.close()
        log.close()
        tls_log.close()
