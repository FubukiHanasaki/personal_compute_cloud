import os
import argparse
import getpass
import logging
import platform
import ssl
from pathlib import Path
import httpx
from .config import AgentConfig
from .platform import save_credential


def main():
    parser = argparse.ArgumentParser(description="PCC Agent · FubukiHanasaki")
    parser.add_argument(
        "command",
        choices=[
            "init",
            "register",
            "run",
            "check",
            "reconcile",
            "cleanup-windows",
            "gui",
        ],
    )
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--confirm-stopped", action="store_true")
    parser.add_argument(
        "--port", type=int, default=0, help="Local GUI port (default: automatic)"
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.command == "gui":
        from .gui import serve

        if not 0 <= args.port <= 65535:
            parser.error("Invalid GUI port")
        serve(args.config, args.port, not args.no_browser)
        return
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if args.command == "init":
        target = Path(args.config)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as file:
            file.write(
                '# Author: FubukiHanasaki\nversion = 1\nserver = "https://compute.example.com"\nname = "my-node"\ncredential_file = "tmp/agent-credential.json"\nwork_root = "tmp/agent"\nmode = "maintenance"\nallocatable_cpu = 1\nallocatable_memory_mb = 512\nenable_compute = false\n'
            )
        return
    if args.command == "run":
        if os.name == "posix" and os.geteuid() == 0:
            raise SystemExit("Agent must run as a dedicated non-root account")
        if os.name == "nt":
            import ctypes

            if ctypes.windll.shell32.IsUserAnAdmin():
                raise SystemExit(
                    "Agent must run as a standard non-administrator account"
                )
    config = AgentConfig.load(args.config)
    if args.command == "cleanup-windows":
        if not args.confirm_stopped:
            raise SystemExit("Stop the owning Agent, then use --confirm-stopped")
        from .windows_sandbox import cleanup_stale_profiles

        print(f"Removed {cleanup_stale_profiles(config)} stopped Windows profiles")
        return
    if args.command == "register":
        raw = getpass.getpass("One-time registration token: ")
        with httpx.Client(
            timeout=20,
            trust_env=False,
            verify=ssl.create_default_context(cafile=str(config.ca_file))
            if config.ca_file
            else True,
        ) as client:
            response = client.post(
                config.server.rstrip("/") + "/internal/v1/nodes/register",
                json={"token": raw, "name": config.name, "os": platform.system()},
            )
            response.raise_for_status()
            save_credential(config.credential_file, response.json())
        print("Registered; credential stored in restricted local file")
    else:
        from .runtime import Agent

        agent = Agent(args.config, probe_isolation=args.command != "reconcile")
        if args.command == "reconcile":
            import json
            import psutil
            import subprocess

            journal = config.work_root / "active-attempt.json"
            record = json.loads(journal.read_text())
            if not args.confirm_stopped:
                raise SystemExit(
                    "Inspect original worker and descendants; pass --confirm-stopped only after verification"
                )
            controller = record.get("controller_pid")
            if controller and psutil.pid_exists(controller):
                try:
                    if psutil.Process(controller).create_time() == record.get(
                        "controller_created_at"
                    ):
                        raise SystemExit(
                            "Owning Agent is still alive; stop it before reconciliation"
                        )
                except psutil.NoSuchProcess:
                    pass
            pid = record.get("pid")
            if pid and psutil.pid_exists(pid):
                process = psutil.Process(pid)
                if process.create_time() == record.get("process_created_at"):
                    raise SystemExit(
                        "Original worker is still alive; reconciliation refused"
                    )
            scope = record.get("managed_scope")
            if scope:
                import re

                if os.name != "posix" or not re.fullmatch(
                    r"pcc-worker-[a-f0-9]{32}", scope
                ):
                    raise SystemExit("Inspect this attempt on its original Linux host")
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", "--quiet", scope + ".scope"],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    raise SystemExit(
                        "Managed scope is still active; reconciliation refused"
                    )
                if result.returncode not in {3, 4}:
                    raise SystemExit("Cannot confirm managed scope stopped")
            if record.get("managed_job"):
                from .windows_managed import active_job

                if os.name != "nt":
                    raise SystemExit(
                        "Inspect this attempt on its original Windows host"
                    )
                if active_job(record["managed_job"]):
                    raise SystemExit(
                        "Managed Windows Job is still active; reconciliation refused"
                    )
            response = agent.client.post(
                "/internal/v1/jobs/" + record["job_id"] + "/fail",
                json={
                    "attempt_id": record["attempt_id"],
                    "lease_token": record["lease_token"],
                    "stopped": True,
                    "retryable": False,
                    "message": "Operator confirmed original worker and descendants stopped",
                },
            )
            response.raise_for_status()
            journal.unlink()
            (config.work_root / "pending-claim.json").unlink(missing_ok=True)
            print("Reconciled as stopped; no automatic retry requested")
        elif args.command == "check":
            from .platform import discover
            import json

            print(
                json.dumps(
                    discover(agent.config, agent.isolation, agent.managed_isolation),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            agent.run(once=args.once)
