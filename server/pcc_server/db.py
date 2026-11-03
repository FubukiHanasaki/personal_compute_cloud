from contextlib import contextmanager
import threading
from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url):
    options = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=options, pool_pre_ping=True)


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def get_db(request: Request):
    with request.app.state.sessions() as db:
        yield db


_sqlite_lock = threading.RLock()


@contextmanager
def transaction(db):
    """Serialize state transitions; PostgreSQL lock spans every server process."""
    is_sqlite = db.bind.dialect.name == "sqlite"
    lock = _sqlite_lock if is_sqlite else __import__("contextlib").nullcontext()
    with lock:
        try:
            if is_sqlite:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                db.execute(text("SELECT pg_advisory_xact_lock(74201904)"))
            db.expire_all()
            yield
            db.commit()
        except BaseException:
            db.rollback()
            raise
