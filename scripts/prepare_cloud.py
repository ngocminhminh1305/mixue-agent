"""Migrate a new cloud database and optionally copy the current local shop records."""
import argparse

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import MetaData, bindparam, create_engine, func, select, text
from sqlalchemy.engine import make_url

from bobaops.core.database import database_url, get_engine
from bobaops.core.paths import ROOT

EXCLUDED = {'alembic_version', 'auth_sessions', 'auth_login_limits'}


def copy_shop(source, target):
    """Copy into migrated, empty app tables. Both arguments are transaction connections."""
    metadata = MetaData()
    metadata.reflect(bind=source, views=False)
    tables = [t for t in metadata.sorted_tables if t.name not in EXCLUDED]
    for table in tables:
        count = target.scalar(select(func.count()).select_from(table))
        if table.name == 'shop_state':
            if count != 1 or target.scalar(text('SELECT revision FROM shop_state WHERE id=1')) != 0:
                raise ValueError('The target shop already contains data.')
        elif count:
            raise ValueError('The target shop already contains data.')
    # Photo transfer is separate. Refuse to leave broken local file references in the cloud.
    if source.scalar(text('SELECT count(*) FROM sales_images')):
        raise ValueError('The source contains receipt photos. Transfer photos before copying this database.')
    target.execute(text('DELETE FROM shop_state'))
    counts = {}
    for table in tables:
        rows = [dict(r) for r in source.execute(select(table)).mappings()]
        if table.name == 'inventory_movements':
            rows.sort(key=lambda r: r['reversal_of'] is not None)
        if rows:
            if any(c.identity is not None and c.identity.always for c in table.columns):
                quote = target.dialect.identifier_preparer.quote
                columns = ', '.join(quote(c.name) for c in table.columns)
                params = ', '.join(':' + c.name for c in table.columns)
                statement = text(f'INSERT INTO {quote(table.name)} ({columns}) OVERRIDING SYSTEM VALUE VALUES ({params})')
                statement = statement.bindparams(*[bindparam(c.name, type_=c.type) for c in table.columns])
                target.execute(statement, rows)
            else:
                target.execute(table.insert(), rows)
        counts[table.name] = len(rows)
    # Identity columns (aliases, questions) need their sequences moved past copied IDs.
    for table in tables:
        for column in table.columns:
            if column.identity is not None:
                sequence = target.scalar(text('SELECT pg_get_serial_sequence(:table, :column)'),
                                         {'table': table.name, 'column': column.name})
                if sequence:
                    highest = target.scalar(select(func.max(column)))
                    if highest is not None:
                        target.execute(text('SELECT setval(CAST(:seq AS regclass), :value, true)'),
                                       {'seq': sequence, 'value': highest})
    return counts


def restrict_data_api(conn):
    roles = list(conn.scalars(text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")))
    for role in roles:  # Names come from this fixed allowlist, not user input.
        conn.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"'))
        conn.execute(text(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM "{role}"'))
        conn.execute(text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM "{role}"'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env.render')
    parser.add_argument('--copy-shop-data', action='store_true', help='Copy current local data into an empty target; existing records are never overwritten.')
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    try:
        target_url = make_url(values.get('DATABASE_URL') or '')
        if (target_url.get_backend_name() not in {'postgresql', 'postgres'}
                or not (target_url.host or '').endswith('.pooler.supabase.com') or target_url.port != 5432):
            raise ValueError()
    except Exception:
        raise SystemExit('Fill DATABASE_URL in .env.render with the Supabase Session pooler URL on port 5432.') from None
    target = create_engine(target_url.set(drivername='postgresql+psycopg'),
                           connect_args={'connect_timeout': 10, 'sslmode': 'require'})
    try:
        with target.begin() as conn:
            cfg = Config(str(ROOT / 'alembic.ini'))
            cfg.attributes['connection'] = conn
            command.upgrade(cfg, 'head')
            counts = None
            if args.copy_shop_data:
                local = database_url()
                if local.host not in {'127.0.0.1', 'localhost'} or local.port != 5433:
                    raise ValueError('Keep local .env pointed at the Mac database.')
                with get_engine().connect().execution_options(isolation_level='REPEATABLE READ') as source, source.begin():
                    source.execute(text('SET TRANSACTION READ ONLY'))
                    counts = copy_shop(source, conn)
            restrict_data_api(conn)
    except Exception:
        raise SystemExit('Cloud preparation failed and rolled back. Check the private connection settings and that the target is empty. Existing shop data is never overwritten. Do not paste credentials into chat.') from None
    finally:
        target.dispose()
    print('Cloud schema is current and app tables are restricted from anonymous Data API access.')
    if counts is not None:
        print(f'Copied {counts.get("products", 0)} products and {counts.get("stock_items", 0)} stock items with their related shop records. Local data is unchanged. Login sessions were not copied.')


if __name__ == '__main__':
    main()
