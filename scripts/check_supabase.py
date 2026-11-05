"""Check private cloud settings without changing local .env or printing secrets."""
import argparse
import os

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from bobaops.core.photos import PhotoStore, PhotoStorageError
from bobaops.core.paths import PRIVATE_DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env.render')
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    if not values.get('DATABASE_URL'):
        raise SystemExit('Enter DATABASE_URL in the private cloud env file first.')
    try:
        url = make_url(values['DATABASE_URL'])
        if url.get_backend_name() not in {'postgres', 'postgresql'} or not (url.host or '').endswith('.supabase.com'):
            raise ValueError()
        if url.port != 5432:
            raise ValueError()
    except Exception:
        raise SystemExit('Use the Supabase Session pooler PostgreSQL URL on port 5432.') from None
    os.environ.update({k: v for k, v in values.items() if v is not None})
    engine = create_engine(url.set(drivername='postgresql+psycopg'), connect_args={'connect_timeout': 10, 'sslmode': 'require'})
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        photos = PhotoStore(PRIVATE_DATA_DIR / 'sales_images')
        if photos.backend != 'supabase':
            raise PhotoStorageError()
        photos.check_private_bucket()
    except Exception:
        raise SystemExit('Cloud check failed. Check the session-pooler URL/password, Supabase URL, server secret key and private receipts bucket. No credentials were printed.') from None
    finally:
        engine.dispose()
    print('Supabase database connection and private receipts bucket verified. No data was changed.')


if __name__ == '__main__':
    main()
