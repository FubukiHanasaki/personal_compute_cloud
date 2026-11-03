import argparse
import getpass
import time
import uvicorn
from sqlalchemy import select
from .config import get_settings
from .db import make_engine, session_factory, transaction
from .models import APIKey, User
from .security import digest, hash_password, token, verify_password


def bootstrap_owner(db, settings):
    if settings.owner_password:
        if len(settings.owner_password) < 16 or settings.owner_password.startswith(
            "REPLACE_"
        ):
            raise ValueError("Owner password must be unique and at least 16 characters")
        with transaction(db):
            if not db.scalar(select(User).where(User.role == "owner")):
                db.add(
                    User(
                        username=settings.owner_username,
                        role="owner",
                        password_hash=hash_password(settings.owner_password),
                    )
                )


def main():
    parser = argparse.ArgumentParser(description="PCC Server · FubukiHanasaki")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve", "bootstrap-key", "cleanup"],
        default="serve",
    )
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "cleanup":
        from .maintenance import cleanup

        with session_factory(make_engine(settings.database_url))() as db:
            print(cleanup(db, settings))
        return
    with session_factory(make_engine(settings.database_url))() as db:
        bootstrap_owner(db, settings)
        if args.command == "bootstrap-key":
            password = getpass.getpass("Owner password: ")
            user = db.scalar(
                select(User).where(
                    User.username == settings.owner_username, User.role == "owner"
                )
            )
            if not user or not verify_password(password, user.password_hash):
                raise SystemExit("Invalid owner credential")
            raw = token("pcc_")
            with transaction(db):
                db.add(
                    APIKey(
                        user_id=user.id,
                        hash=digest(raw, settings),
                        scopes=["admin"],
                        expires_at=time.time() + 3600,
                    )
                )
            print(raw)
            return
    uvicorn.run(
        "pcc_server.app:create_app",
        factory=True,
        host=settings.bind_host,
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
    )
