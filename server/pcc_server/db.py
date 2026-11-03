from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url):
    options = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=options, pool_pre_ping=True)


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def get_db(request):
    with request.app.state.sessions() as db:
        yield db
