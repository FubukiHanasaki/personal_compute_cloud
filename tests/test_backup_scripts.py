"""Compose wrapper failure ordering; real DB restore is tested separately. FubukiHanasaki."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux deployment scripts")


def docker_fixture(tmp_path):
    tool = tmp_path / "docker"
    tool.write_text(f"""#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["DOCKER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[1] == "ps":
    if os.environ.get("WAS_RUNNING") == "1": print("test-server")
elif args[1] == "exec":
    if "pg_dump" in args:
        if os.environ.get("FAIL_DUMP") == "1": sys.exit(7)
        sys.stdout.buffer.write(b"test-dump")
elif args[1] == "run":
    command = args[args.index("server") + 1:]
    if "python" in args:
        command = [sys.executable] + command
        command[-1] = os.environ["ARTIFACT_ROOT"]
    else:
        command = ["sh"] + command
        command[-1] = command[-1].replace("/data/artifacts", os.environ["ARTIFACT_ROOT"]).replace("/tmp/pcc-restore.tar.gz", os.environ["RESTORE_ARCHIVE"])
    sys.exit(subprocess.run(command).returncode)
""")
    tool.chmod(0o700)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    return {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        "ARTIFACT_ROOT": str(artifacts),
        "RESTORE_ARCHIVE": str(tmp_path / "restore.tar.gz"),
        "PYTHONPATH": str(Path("server").resolve()),
    }


@pytest.mark.parametrize("running", ["0", "1"])
def test_failed_backup_keeps_incomplete_marker_and_original_service_state(
    tmp_path, running
):
    env = {**docker_fixture(tmp_path), "FAIL_DUMP": "1", "WAS_RUNNING": running}
    result = subprocess.run(
        ["sh", "deploy/backup.sh", str(tmp_path / "backup")],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0
    assert (tmp_path / "backup/.incomplete").exists()
    commands = [
        json.loads(line) for line in Path(env["DOCKER_LOG"]).read_text().splitlines()
    ]
    assert (["compose", "start", "server"] in commands) == (running == "1")


def test_archive_rejection_precedes_pg_restore(tmp_path):
    env = docker_fixture(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "pcc.dump").write_bytes(b"test")
    with tarfile.open(backup / "artifacts.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo("../../escape")
        archive.addfile(member, io.BytesIO())
    (backup / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((backup / name).read_bytes()).hexdigest()}  {name}\n"
            for name in ("pcc.dump", "artifacts.tar.gz")
        )
    )
    result = subprocess.run(
        ["sh", "deploy/restore.sh", str(backup)], env=env, capture_output=True
    )
    assert result.returncode != 0
    commands = Path(env["DOCKER_LOG"]).read_text()
    assert "pg_restore" not in commands
    assert not (tmp_path / "escape").exists()
