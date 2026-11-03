import time
from sqlalchemy import delete, select
from .db import transaction
from .models import Artifact, LoginBucket, WebSession
from .security import audit
from .storage import LocalStorage


def cleanup(db, settings):
    storage = LocalStorage(settings.storage_root)
    removed = 0
    with transaction(db):
        for artifact in db.scalars(
            select(Artifact).where(Artifact.expires_at <= time.time())
        ):
            storage.delete(artifact.key)
            db.delete(artifact)
            removed += 1
        db.execute(delete(WebSession).where(WebSession.expires_at <= time.time()))
        db.execute(delete(LoginBucket).where(LoginBucket.resets_at <= time.time()))
        # Remove crash-orphaned files only after a grace period.
        referenced = set(db.scalars(select(Artifact.key)))
        for file in storage.root.iterdir():
            if (
                file.name not in referenced
                and file.is_file()
                and not file.is_symlink()
                and file.stat().st_mtime < time.time() - 86400
            ):
                storage.delete(file.name)
                removed += 1
        audit(db, "server", "storage.cleanup", "artifacts")
    return {"removed": removed}
