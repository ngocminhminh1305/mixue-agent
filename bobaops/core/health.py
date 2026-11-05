"""Minimal public probes; configuration and database errors stay on the server."""
import os

from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from bobaops.auth.security import load_settings
from bobaops.core.database import get_engine, storage_backend
from bobaops.core.paths import PRIVATE_DATA_DIR, ROOT
from bobaops.core.photos import PhotoStore, PhotoStorageError

router = APIRouter()


@router.get('/health/live', include_in_schema=False)
def live():
    return {'status': 'ok'}


@router.get('/health/ready', include_in_schema=False)
def ready():
    try:
        if not load_settings().configured or storage_backend() != 'postgres':
            raise ValueError('Not configured')
        photos = PhotoStore(PRIVATE_DATA_DIR / 'sales_images')
        if photos.backend == 'supabase':
            photos.check_private_bucket()
        elif not PRIVATE_DATA_DIR.is_dir() or not os.access(PRIVATE_DATA_DIR, os.W_OK | os.X_OK):
            raise ValueError('Storage unavailable')
        expected = ScriptDirectory(str(ROOT / 'migrations')).get_current_head()
        with get_engine().connect() as conn:
            revisions = set(conn.execute(text('SELECT version_num FROM alembic_version')).scalars())
        if revisions != {expected}:
            raise ValueError('Migration required')
    except (SQLAlchemyError, ValueError, OSError, CommandError, PhotoStorageError):
        return JSONResponse({'status': 'unavailable'}, status_code=503)
    return {'status': 'ok'}
