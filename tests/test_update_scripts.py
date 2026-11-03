"""Exercise updater against disposable local Git remotes. Author: FubukiHanasaki."""

import importlib.util
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location(
    "pcc_updater", Path(__file__).resolve().parents[1] / "scripts/update.py"
)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def run(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    remote, source, checkout = (
        tmp_path / name for name in ("remote.git", "source", "checkout")
    )
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    source.mkdir()
    run(source, "init", "-b", "main")
    for key, value in (
        ("user.name", "FubukiHanasaki"),
        ("user.email", "39644949+FubukiHanasaki@users.noreply.github.com"),
    ):
        run(source, "config", key, value)
    (source / ".gitignore").write_text("tmp/\nagent.toml\n")
    (source / "app.py").write_text("version=1\n")
    run(source, "add", ".")
    run(source, "commit", "-m", "initial")
    run(source, "remote", "add", "origin", str(remote))
    run(source, "push", "-u", "origin", "main")
    subprocess.run(
        ["git", "clone", "-b", "main", str(remote), str(checkout)],
        check=True,
        capture_output=True,
    )
    for key, value in (
        ("user.name", "FubukiHanasaki"),
        ("user.email", "39644949+FubukiHanasaki@users.noreply.github.com"),
    ):
        run(checkout, "config", key, value)
    (source / "app.py").write_text("version=2\n")
    run(source, "add", ".")
    run(source, "commit", "-m", "update")
    run(source, "push")
    monkeypatch.setattr(updater, "ROOT", checkout)
    monkeypatch.setattr(updater, "REMOTES", {str(remote)})
    return source, checkout


def test_check_only_then_fast_forward_preserves_local_config(repositories):
    _, checkout = repositories
    (checkout / "agent.toml").write_text("local configuration")
    old = run(checkout, "rev-parse", "HEAD")
    assert updater.main([]) == 0
    assert run(checkout, "rev-parse", "HEAD") == old
    assert updater.main(["--apply", "--confirm-stopped"]) == 0
    assert (checkout / "app.py").read_text() == "version=2\n"
    assert (checkout / "agent.toml").read_text() == "local configuration"
    assert not (checkout / "tmp/update.lock").exists()


@pytest.mark.parametrize("change", ["tracked", "untracked", "ahead"])
def test_refuses_local_changes_without_reset_or_stash(repositories, change):
    _, checkout = repositories
    path = checkout / ("local.py" if change == "untracked" else "app.py")
    path.write_text("keep me\n")
    if change == "ahead":
        run(checkout, "add", ".")
        run(checkout, "commit", "-m", "local work")
    before = run(checkout, "rev-parse", "HEAD")
    assert updater.main(["--apply", "--confirm-stopped"]) == 1
    assert path.read_text() == "keep me\n"
    assert run(checkout, "rev-parse", "HEAD") == before


def test_refuses_wrong_remote_branch_and_parallel_update(repositories):
    _, checkout = repositories
    run(checkout, "remote", "set-url", "origin", "https://example.com/other.git")
    with pytest.raises(updater.UpdateError, match="官方仓库"):
        updater.inspect(checkout, fetch=False)
    with updater.update_lock(checkout):
        with pytest.raises(updater.UpdateError, match="已有更新"):
            with updater.update_lock(checkout):
                pytest.fail("second update acquired the lock")
    assert not (checkout / "tmp/update.lock").exists()


def test_apply_requires_explicit_stop_and_valid_install_target():
    with pytest.raises(SystemExit):
        updater.main(["--apply"])
    with pytest.raises(SystemExit):
        updater.main(["--install"])


@pytest.mark.parametrize("use_uv", [False, True])
def test_agent_install_targets_existing_venv_and_keeps_caches_in_tmp(
    tmp_path, monkeypatch, use_uv
):
    suffix = (
        Path("Scripts/python.exe") if updater.os.name == "nt" else Path("bin/python")
    )
    interpreter = tmp_path / "tmp/agent-venv" / suffix
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    calls = []
    monkeypatch.setattr(
        updater.shutil, "which", lambda name: "approved-uv" if use_uv else None
    )
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    updater.install(tmp_path, "agent")
    assert calls
    for command, options in calls:
        assert options["cwd"] == tmp_path and options["check"]
        assert Path(options["env"]["UV_CACHE_DIR"]).is_relative_to(tmp_path / "tmp")
        if use_uv:
            assert (
                Path(options["env"]["UV_PROJECT_ENVIRONMENT"])
                == interpreter.parent.parent
            )
            assert "--frozen" in command
        else:
            assert command[0] == str(interpreter)


def test_help_survives_legacy_windows_pipe_encoding(tmp_path):
    # Reproduce a redirected Windows cp1252 console on any platform, without Git
    # or network activity: argparse exits before any update operation.
    import os
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts/update.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        env={
            **os.environ,
            "PYTHONIOENCODING": "cp1252",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        cwd=tmp_path,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert "检查" in result.stdout.decode("utf-8")
    assert not list(tmp_path.iterdir())
