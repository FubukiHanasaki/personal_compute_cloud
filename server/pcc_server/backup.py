"""Portable, verified database/artifact snapshots. Author: Fubuki."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pg_env(url):
    parsed = make_url(url)
    if (
        not parsed.drivername.startswith("postgresql")
        or parsed.query
        or not all((parsed.host, parsed.username, parsed.database))
    ):
        raise ValueError(
            "Use an explicit PostgreSQL host/user/database URL without query options"
        )
    return {
        **os.environ,
        "PGHOST": parsed.host or "localhost",
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": parsed.username or "pcc",
        "PGPASSWORD": parsed.password or "",
        "PGDATABASE": parsed.database or "pcc",
    }


def pack(root, target):
    root = Path(root).resolve(strict=True)
    with tarfile.open(fileobj=target, mode="w|gz") as archive:
        for path in sorted(root.iterdir()):
            if (
                path.is_symlink()
                or not path.is_file()
                or not re.fullmatch(r"[a-f0-9]{32}", path.name)
            ):
                raise ValueError("Artifact directory contains unexpected entries")
            archive.add(path, arcname="artifacts/" + path.name, recursive=False)


def safe_members(archive, max_bytes=100 * 1024**3):
    names, total = set(), 0
    for member in archive:
        if (
            not member.isfile()
            or not re.fullmatch(r"artifacts/[a-f0-9]{32}", member.name)
            or member.name in names
        ):
            raise ValueError("Unsafe or duplicate artifact archive entry")
        names.add(member.name)
        total += member.size
        if member.size < 0 or total > max_bytes or len(names) > 1000000:
            raise ValueError("Artifact archive exceeds restore limit")
        yield member


def validate_archive(path, max_bytes=100 * 1024**3):
    with tarfile.open(path, "r:gz") as archive:
        return {member.name for member in safe_members(archive, max_bytes)}


def validate_stream(stream):
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for _ in safe_members(archive):
            pass


def unpack(archive_path, root):
    validate_archive(archive_path)
    with Path(archive_path).open("rb") as stream:
        unpack_stream(stream, root)


def unpack_stream(stream, root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Restore target must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("Restore artifact directory must be empty")
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for member in safe_members(archive):
            path = root / member.name.split("/")[1]
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as output:
                shutil.copyfileobj(
                    archive.extractfile(member), output, length=1024 * 1024
                )


def create(destination, storage_root, database_url, pg_dump="pg_dump", quiesced=False):
    if not quiesced:
        raise ValueError("Stop all Server writers and confirm quiesced before snapshot")
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("Backup destination must be new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="snapshot-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        staging.chmod(0o700)
        subprocess.run(
            [
                str(pg_dump),
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(staging / "pcc.dump"),
            ],
            env=pg_env(database_url),
            check=True,
            capture_output=True,
        )
        with (staging / "artifacts.tar.gz").open("wb") as stream:
            pack(storage_root, stream)
        validate_archive(staging / "artifacts.tar.gz")
        manifest = {
            "version": 1,
            "author": "Fubuki",
            "created_at": time.time(),
            "files": {
                name: sha256(staging / name)
                for name in ("pcc.dump", "artifacts.tar.gz")
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        (staging / "SHA256SUMS").write_text(
            "".join(
                f"{checksum}  {name}\n" for name, checksum in manifest["files"].items()
            ),
            encoding="ascii",
        )
        # Same-filesystem rename publishes only a completely generated snapshot.
        staging.rename(destination)
    return destination


def verify(source):
    source = Path(source)
    if (source / ".incomplete").exists():
        raise ValueError("Incomplete snapshot refused")
    manifest_path = source / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Manifest must be a regular file")
    if manifest_path.stat().st_size > 10000:
        raise ValueError("Manifest too large")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or set(manifest.get("files", {})) != {
        "pcc.dump",
        "artifacts.tar.gz",
    }:
        raise ValueError("Unsupported snapshot manifest")
    for name, checksum in manifest["files"].items():
        path = source / name
        if path.is_symlink() or not path.is_file() or sha256(path) != checksum:
            raise ValueError("Backup checksum mismatch: " + name)
    validate_archive(source / "artifacts.tar.gz")
    return manifest


def check_restore_target(storage_root, database_url):
    """Shared preflight for native and Compose restores; never merge live data."""
    pg_env(database_url)
    storage_root = Path(storage_root)
    if storage_root.is_symlink():
        raise ValueError("Restore artifact directory must not be a symlink")
    if storage_root.exists() and any(storage_root.iterdir()):
        raise ValueError("Restore requires a new empty artifact directory")
    parsed = make_url(database_url)
    engine = create_engine(parsed.set(port=parsed.port or 5432))
    try:
        with engine.connect() as connection:
            count = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S')"
                )
            )
            if count:
                raise ValueError("Restore requires an empty target database")
    finally:
        engine.dispose()


def restore(
    source, storage_root, database_url, pg_restore="pg_restore", quiesced=False
):
    if not quiesced:
        raise ValueError("Stop all Server writers and confirm quiesced before restore")
    environment = pg_env(database_url)
    source, storage_root = Path(source), Path(storage_root)
    verify(source)
    check_restore_target(storage_root, database_url)
    # Verify/extract before mutating the DB. A failed restore stays offline and
    # must be discarded/retried into a fresh target, never served as complete.
    unpack(source / "artifacts.tar.gz", storage_root)
    subprocess.run(
        [
            str(pg_restore),
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            "--dbname",
            environment["PGDATABASE"],
            str(source / "pcc.dump"),
        ],
        env=environment,
        check=True,
        capture_output=True,
    )


def main():
    import sys

    parser = argparse.ArgumentParser(description="PCC verified backups · Fubuki")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "restore"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        command.add_argument("--storage-root", type=Path, required=True)
        command.add_argument("--quiesced", action="store_true", required=True)
        command.add_argument("--pg-bin", type=Path)
    command = commands.add_parser("verify")
    command.add_argument("directory", type=Path)
    command = commands.add_parser("pack")
    command.add_argument("directory", type=Path)
    command = commands.add_parser("unpack")
    command.add_argument("archive", type=Path)
    command.add_argument("directory", type=Path)
    command = commands.add_parser("verify-archive")
    command.add_argument("archive", type=Path)
    command = commands.add_parser("check-restore-target")
    command.add_argument("directory", type=Path)
    commands.add_parser("verify-stream")
    command = commands.add_parser("unpack-stream")
    command.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "pack":
        pack(args.directory, sys.stdout.buffer)
    elif args.command == "verify-stream":
        validate_stream(sys.stdin.buffer)
    elif args.command == "unpack-stream":
        unpack_stream(sys.stdin.buffer, args.directory)
    elif args.command == "unpack":
        unpack(args.archive, args.directory)
    elif args.command == "verify-archive":
        validate_archive(args.archive)
    elif args.command == "verify":
        verify(args.directory)
    elif args.command == "check-restore-target":
        check_restore_target(args.directory, os.environ["PCC_DATABASE_URL"])
    else:
        url = os.environ["PCC_DATABASE_URL"]
        executable = "pg_dump" if args.command == "create" else "pg_restore"
        executable = args.pg_bin / executable if args.pg_bin else executable
        function = create if args.command == "create" else restore
        function(
            args.directory, args.storage_root, url, executable, quiesced=args.quiesced
        )


if __name__ == "__main__":
    main()
