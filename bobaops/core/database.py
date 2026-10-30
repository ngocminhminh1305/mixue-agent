"""Database configuration; credentials are never logged or placed in command arguments."""
import os
from functools import lru_cache

from dotenv import dotenv_values
from sqlalchemy import MetaData, URL, create_engine

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
    password = env.get('POSTGRES_PASSWORD') or None
    return URL.create('postgresql+psycopg', username=env.get('POSTGRES_USER', 'bobaops'),
                      password=password, host=env.get('POSTGRES_HOST', '127.0.0.1'),
                      port=int(env.get('POSTGRES_PORT', '5433')), database=env.get('POSTGRES_DB', 'bobaops'))


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(database_url(), pool_pre_ping=True, connect_args={'connect_timeout': 5})


@lru_cache(maxsize=1)
def get_tables():
    metadata = MetaData()
    metadata.reflect(bind=get_engine())
    return metadata.tables
