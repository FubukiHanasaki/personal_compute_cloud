"""Conservative GitHub checkout updater. Author: FubukiHanasaki."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REMOTES = {
    "https://github.com/FubukiHanasaki/personal_compute_cloud.git",
    "https://github.com/FubukiHanasaki/personal_compute_cloud",
    "git@github.com:FubukiHanasaki/personal_compute_cloud.git",
    "ssh://git@github.com/FubukiHanasaki/personal_compute_cloud.git",
}


class UpdateError(RuntimeError):
    pass


def git(root, *args):
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        # Don't expose remote URLs/credentials from subprocess stderr.
        raise UpdateError(
            f"Git 操作失败：{args[0]}；请在本机检查网络、Git 权限及仓库状态"
        )
    return result.stdout.strip()


@contextmanager
def update_lock(root):
    folder = root / "tmp"
    folder.mkdir(exist_ok=True)
    lock = folder / "update.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise UpdateError(
            "已有更新操作，或上次更新中断；确认无更新进程后处理 tmp/update.lock"
        ) from exc
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def inspect(root, fetch=True):
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise UpdateError("请在项目源码检出目录运行更新")
    if git(root, "remote", "get-url", "origin") not in REMOTES:
        raise UpdateError(
            "origin 必须指向 FubukiHanasaki/personal_compute_cloud 官方仓库"
        )
    if git(root, "symbolic-ref", "--short", "HEAD") != "main":
        raise UpdateError("仅支持 main 分支；不会切换分支或重置本地修改")
    if fetch:
        git(
            root,
            "fetch",
            "--no-tags",
            "origin",
            "refs/heads/main:refs/remotes/origin/main",
        )
    ahead, behind = map(
        int,
        git(root, "rev-list", "--left-right", "--count", "HEAD...origin/main").split(),
    )
    return {
        "current": git(root, "rev-parse", "HEAD"),
        "latest": git(root, "rev-parse", "origin/main"),
        "ahead": ahead,
        "behind": behind,
        "dirty": bool(git(root, "status", "--porcelain", "--untracked-files=normal")),
    }


def install(root, target):
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "UV_CACHE_DIR": str(root / "tmp/uv-cache"),
        "UV_PROJECT_ENVIRONMENT": str(root / "tmp/venv"),
        "TMPDIR": str(root / "tmp"),
        "TMP": str(root / "tmp"),
        "TEMP": str(root / "tmp"),
        "npm_config_cache": str(root / "tmp/npm-cache"),
    }
    if target == "agent":
        suffix = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
        interpreter = next(
            (
                root / "tmp" / folder / suffix
                for folder in ("agent-venv", "venv")
                if (root / "tmp" / folder / suffix).is_file()
            ),
            None,
        )
        if interpreter is None:
            raise UpdateError(
                "未找到 tmp/agent-venv 或 tmp/venv，请先按安装文档创建环境"
            )
        uv = shutil.which("uv")
        if uv:
            env["UV_PROJECT_ENVIRONMENT"] = str(interpreter.parent.parent)
            commands = [[uv, "sync", "--frozen"]]
        else:
            # uv-created environments can omit pip. Bootstrap only inside the
            # selected existing venv; never fall back to the system interpreter.
            commands = [
                [str(interpreter), "-m", "ensurepip", "--upgrade"],
                [
                    str(interpreter),
                    "-m",
                    "pip",
                    "install",
                    "--cache-dir",
                    str(root / "tmp/pip-cache"),
                    ".",
                ],
            ]
    elif target == "server":
        commands = [
            [sys.executable, "scripts/dev.py", "setup"],
            [sys.executable, "scripts/dev.py", "build"],
        ]
    else:
        raise UpdateError("--install 需要 --target agent 或 server")
    for command in commands:
        subprocess.run(command, cwd=root, env=env, check=True)


def main(argv=None):
    # Windows CI captures stdout via a pipe, which can default to a legacy code
    # page. The help/error text is Chinese; configure both streams before argparse.
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if configure:
            configure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="PCC GitHub 更新 · FubukiHanasaki")
    parser.add_argument("--apply", action="store_true", help="快进拉取；默认只检查")
    parser.add_argument(
        "--confirm-stopped", action="store_true", help="已停止本机 PCC 运行进程"
    )
    parser.add_argument(
        "--target", choices=["source", "agent", "server"], default="source"
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="拉取后安装所选目标依赖；Server 同时构建前端",
    )
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_stopped:
        parser.error(
            "拉取前先停止本机 PCC，再传 --confirm-stopped；脚本不会停止其他进程"
        )
    if args.install and (not args.apply or args.target == "source"):
        parser.error("安装需要 --apply 和 --target agent/server")
    try:
        with update_lock(ROOT):
            status = inspect(ROOT)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            if not args.apply:
                print("有更新可用" if status["behind"] else "已是最新源码")
                if status["dirty"] or status["ahead"]:
                    print("本地存在修改或独有提交；自动拉取将拒绝执行")
                return 0
            if status["dirty"] or status["ahead"]:
                raise UpdateError(
                    "本地存在修改或独有提交，拒绝拉取；请先自行提交或保存改动"
                )
            previous = status["current"]
            # A named remote ref can move. Merge the exact inspected commit.
            git(ROOT, "merge", "--ff-only", status["latest"])
            print(f"源码：{previous[:12]} → {status['latest'][:12]}")
            if args.install:
                install(ROOT, args.target)
            print("更新完成。未启动进程、未迁移数据库；检查部署配置后再手动启动。")
            return 0
    except (UpdateError, OSError, subprocess.SubprocessError) as exc:
        print(f"更新未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
