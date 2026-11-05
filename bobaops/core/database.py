"""Database configuration; credentials are never logged or placed in command arguments."""
import os
from functools import lru_cache

from dotenv import dotenv_values
from sqlalchemy import MetaData, URL, create_engine
from sqlalchemy.engine import make_url

from bobaops.core.paths import ROOT


def environment():
    return {**dotenv_values(ROOT / '.env'), **os.environ}


def storage_backend():
    backend = environment().get('SHOP_STORAGE', 'json')
    if backend not in {'json', 'postgres'}:
        raise ValueError('SHOP_STORAGE must be json or postgres.')
    return backend


def database_url():
    env = environment()
    if env.get('DATABASE_URL'):
        url = make_url(env['DATABASE_URL'])
        if url.get_backend_name() not in {'postgres', 'postgresql'}:
            raise ValueError('DATABASE_URL must be a PostgreSQL connection URL.')
        return url.set(drivername='postgresql+psycopg')
    password = env.get('POSTGRES_PASSWORD') or None
    return URL.create('postgresql+psycopg', username=env.get('POSTGRES_USER', 'bobaops'),
                      password=password, host=env.get('POSTGRES_HOST', '127.0.0.1'),
                      port=int(env.get('POSTGRES_PORT', '5433')), database=env.get('POSTGRES_DB', 'bobaops'))


@lru_cache(maxsize=1)
def get_engine():
    env = environment()
    options = {'connect_timeout': 5}
    if env.get('POSTGRES_SSLMODE'):
        options['sslmode'] = env['POSTGRES_SSLMODE']
    if env.get('POSTGRES_SSLROOTCERT'):
        options['sslrootcert'] = env['POSTGRES_SSLROOTCERT']
    # Small connection budget for a single shop and hosted PostgreSQL.
    return create_engine(database_url(), pool_pre_ping=True, pool_size=3, max_overflow=2,
                         pool_timeout=10, connect_args=options)


@lru_cache(maxsize=1)
def get_tables():
    metadata = MetaData()
    metadata.reflect(bind=get_engine())
    return metadata.tables
