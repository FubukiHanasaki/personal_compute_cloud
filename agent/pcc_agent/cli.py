import argparse
import getpass
import logging
import platform
from pathlib import Path
import httpx
from .config import AgentConfig
from .platform import save_credential


def main():
    parser = argparse.ArgumentParser(description="PCC Agent · Fubuki")
    parser.add_argument("command", choices=["init", "register", "run", "check"])
    parser.add_argument("--config", default="agent.toml")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "init":
        target = Path(args.config)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as file:
            file.write('# Author: Fubuki\nversion = 1\nserver = "https://compute.example.com"\nname = "my-node"\ncredential_file = "tmp/agent-credential.json"\nwork_root = "tmp/agent"\nmode = "maintenance"\nallocatable_cpu = 1\nallocatable_memory_mb = 512\nenable_compute = false\n')
        return
    config = AgentConfig.load(args.config)
    if args.command == "register":
        raw = getpass.getpass("One-time registration token: ")
        with httpx.Client(timeout=20, trust_env=False) as client:
            response = client.post(config.server.rstrip("/")+"/internal/v1/nodes/register", json={"token": raw, "name": config.name, "os": platform.system()})
            response.raise_for_status()
            save_credential(config.credential_file, response.json())
        print("Registered; credential stored in restricted local file")
    else:
        from .runtime import Agent
        agent = Agent(args.config)
        if args.command == "check":
            from .platform import discover
            import json
            print(json.dumps(discover(agent.config, agent.isolation), ensure_ascii=False, indent=2))
        else:
            agent.run(once=args.once)
