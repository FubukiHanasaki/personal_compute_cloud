from alembic import context
from pcc_server.config import get_settings
from pcc_server.db import Base, make_engine
from pcc_server import models  # noqa: F401

config = context.config
if context.is_offline_mode():
    context.configure(url=get_settings().database_url, target_metadata=Base.metadata,
                      literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with make_engine(get_settings().database_url).connect() as conn:
        context.configure(connection=conn, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
