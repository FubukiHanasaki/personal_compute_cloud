"""Reproducible local setup; transient files stay in tmp. Author: FubukiHanasaki."""

import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "tmp"


def main():
    parser = argparse.ArgumentParser(description="PCC development · FubukiHanasaki")
    parser.add_argument(
        "command", choices=["setup", "init-dev", "serve", "test", "build", "migrate"]
    )
    args = parser.parse_args()
    TMP.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "UV_PROJECT_ENVIRONMENT": str(TMP / "venv"),
        "UV_CACHE_DIR": str(TMP / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(TMP / "python"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "npm_config_cache": str(TMP / "npm-cache"),
    }
    bindir = TMP / "venv" / ("Scripts" if os.name == "nt" else "bin")

    def run(command):
        subprocess.run([str(arg) for arg in command], cwd=ROOT, env=env, check=True)

    if args.command == "init-dev":
        target = ROOT / ".env"
        with target.open("x", encoding="utf-8") as file:
            file.write(
                f"PCC_ENVIRONMENT=development\nPCC_SECRET={secrets.token_hex(32)}\nPCC_SECURE_COOKIES=false\nPCC_OWNER_USERNAME=Fubuki\nPCC_OWNER_PASSWORD={secrets.token_urlsafe(24)}\nPCC_DATABASE_URL=sqlite:///tmp/pcc.sqlite\n"
            )
        target.chmod(0o600)
        print(
            "Development config created in .env; retrieve the generated owner password there."
        )
    elif args.command == "setup":
        run(["uv", "sync", "--extra", "test", "--frozen"])
        deps = TMP / "webdeps"
        deps.mkdir(exist_ok=True)
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(ROOT / "web" / name, deps / name)
        run(
            [
                shutil.which("npm") or "npm",
                "ci",
                "--prefix",
                deps,
                "--no-audit",
                "--no-fund",
            ]
        )
        link = ROOT / "web" / "node_modules"
        if link.exists() or link.is_symlink():
            if not link.samefile(deps / "node_modules"):
                raise SystemExit("Move existing web/node_modules into tmp before setup")
        elif os.name == "nt":
            run(["cmd", "/c", "mklink", "/J", link, deps / "node_modules"])
        else:
            link.symlink_to(deps / "node_modules", target_is_directory=True)
    elif args.command == "build":
        run([shutil.which("npm") or "npm", "run", "build", "--prefix", "web"])
    else:
        commands = {
            "serve": [bindir / "pcc-server"],
            "test": [bindir / "python", "-m", "pytest"],
            "migrate": [bindir / "alembic", "upgrade", "head"],
        }
        run(commands[args.command])


if __name__ == "__main__":
    main()
