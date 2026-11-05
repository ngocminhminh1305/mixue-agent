from alembic import context
from bobaops.core.database import database_url, get_engine

if context.is_offline_mode():
    context.configure(url=database_url(), literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    def migrate(connection):
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    supplied = context.config.attributes.get('connection')
    if supplied is not None:
        migrate(supplied)
    else:
        with get_engine().connect() as connection:
            migrate(connection)
