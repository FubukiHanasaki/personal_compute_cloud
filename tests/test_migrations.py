import os
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from pcc_server.config import get_settings


def test_versioned_migrations_upgrade_downgrade(monkeypatch, tmp_path):
    postgres = os.environ.get("PCC_TEST_DATABASE_URL")
    engine = None
    if postgres:
        engine = create_engine(postgres)
        schema = "migration_" + uuid.uuid4().hex
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = str(
            make_url(postgres)
            .update_query_dict({"options": "-csearch_path=" + schema})
            .render_as_string(hide_password=False)
        )
    else:
        url = f"sqlite:///{tmp_path}/migrations.sqlite"
    monkeypatch.setenv("PCC_DATABASE_URL", url)
    monkeypatch.setenv("PCC_ENVIRONMENT", "development")
    monkeypatch.setenv("PCC_SECRET", "migration-test-only-" * 3)
    get_settings.cache_clear()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()
        if engine:
            with engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()
