from alembic import context
from bobaops.core.database import database_url, get_engine

if context.is_offline_mode():
    context.configure(url=database_url(), literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    with get_engine().connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
