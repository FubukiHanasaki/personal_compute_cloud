"""Restore drills and malicious archive rejection. Author: FubukiHanasaki."""

import base64
import hashlib
import io
import os
from pathlib import Path
import tarfile
import pytest
from pcc_server.backup import create, restore, verify, validate_archive, unpack


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", "file"),
        ("/escape", "file"),
        ("artifacts/" + "a" * 32, "symlink"),
        ("artifacts/" + "a" * 32, "hardlink"),
    ],
)
def test_restore_rejects_traversal_and_links(tmp_path, name, kind):
    path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        entry = tarfile.TarInfo(name)
        if kind != "file":
            entry.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            entry.linkname = "../../private"
        archive.addfile(entry, io.BytesIO())
    with pytest.raises(ValueError):
        unpack(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_restore_rejects_duplicates_and_size_limit(tmp_path):
    path = tmp_path / "duplicate.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for _ in range(2):
            member = tarfile.TarInfo("artifacts/" + "a" * 32)
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="duplicate"):
        validate_archive(path)
    with pytest.raises(ValueError, match="limit"):
        validate_archive(path, max_bytes=0)


def test_snapshot_requires_quiescence_and_never_overwrites(tmp_path):
    from pcc_server.backup import pg_env

    with pytest.raises(ValueError, match="explicit"):
        pg_env("postgresql:///ambiguous")
    with pytest.raises(ValueError, match="quiesced"):
        create(tmp_path / "backup", tmp_path, "postgresql://unused")
    with pytest.raises(FileExistsError):
        create(tmp_path, tmp_path, "postgresql://unused", quiesced=True)


def test_real_postgresql_snapshot_restores_auth_job_and_artifact(env, tmp_path):
    url = os.environ.get("PCC_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql") or not os.environ.get("PCC_PG_BIN"):
        pytest.skip(
            "Restore drill requires dedicated PostgreSQL and matching pg_dump/pg_restore"
        )
    from pcc_server.db import Base
    from conftest import register_node
    from test_jobs import template, submit, claim, lease
    from test_auth import login

    app, client, auth, _ = env
    template(client, auth)
    _, node, _ = register_node(client, auth)
    job = submit(client, auth).json()["job_id"]
    attempt = claim(client, node).json()
    data = b'{"restored":true}'
    artifact = client.post(
        f"/internal/v1/jobs/{job}/artifacts",
        headers=node,
        json={
            **lease(attempt),
            "name": "restore.json",
            "data_base64": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    ).json()["artifact_id"]
    assert (
        client.post(
            f"/internal/v1/jobs/{job}/complete",
            headers=node,
            json={
                **lease(attempt),
                "result": {"restored": True},
                "artifact_ids": [artifact],
            },
        ).status_code
        == 200
    )
    clients = Path(os.environ["PCC_PG_BIN"])
    source = tmp_path / "snapshot"
    storage = app.state.settings.storage_root
    create(source, storage, url, clients / "pg_dump", quiesced=True)
    assert verify(source)["version"] == 1
    with pytest.raises(ValueError, match="empty"):
        restore(
            source,
            tmp_path / "not-empty-db",
            url,
            clients / "pg_restore",
            quiesced=True,
        )
    Base.metadata.drop_all(app.state.engine)
    storage.rename(tmp_path / "old-artifacts")
    restore(source, storage, url, clients / "pg_restore", quiesced=True)
    login(client)
    assert (
        client.get(f"/api/v1/jobs/{job}", headers=auth).json()["status"] == "completed"
    )
    assert client.get(f"/api/v1/artifacts/{artifact}", headers=auth).content == data
    assert (
        client.post(
            f"/internal/v1/jobs/{job}/renew", headers=node, json=lease(attempt)
        ).status_code
        == 409
    )
    # Same bundle remains verifiable after moving to another backup location.
    moved = tmp_path / "moved"
    source.rename(moved)
    verify(moved)
    with (moved / "pcc.dump").open("ab") as file:
        file.write(b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        verify(moved)


def test_artifact_restore_supports_nonseekable_streams_without_tmp_archive(tmp_path):
    from pcc_server.backup import pack, validate_stream, unpack_stream

    source = tmp_path / "source"
    source.mkdir()
    payload = b"verified-stream" * 10000
    (source / ("a" * 32)).write_bytes(payload)
    compressed = io.BytesIO()
    pack(source, compressed)

    class Pipe(io.BytesIO):
        def seek(self, *args):
            raise AssertionError("Restore must work with stdin, not seek or spool")

    validate_stream(Pipe(compressed.getvalue()))
    target = tmp_path / "restored"
    unpack_stream(Pipe(compressed.getvalue()), target)
    assert (target / ("a" * 32)).read_bytes() == payload
