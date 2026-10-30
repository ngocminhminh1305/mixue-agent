"""Shop setup API and the optional private JSON prototype store."""
import os
import threading

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from bobaops.catalog.schemas import ShopConfig, check_config
from bobaops.core.database import storage_backend
from bobaops.core.paths import PRIVATE_DATA_DIR

STORE = PRIVATE_DATA_DIR / 'shop_config.json'
LOCK = threading.Lock()
router = APIRouter()


def load_config():
    if not STORE.exists():
        return ShopConfig(revision=0, stock_items=[], products=[], preparations=[], questions=[])
    return ShopConfig.model_validate_json(STORE.read_text())


@router.get('/api/shop-config')
def get_config():
    if storage_backend() == 'postgres':
        from bobaops.catalog.repository import read_config
        try:
            return read_config()
        except (SQLAlchemyError, KeyError, ValueError):
            raise HTTPException(503, 'Database is not ready. Start PostgreSQL, run migrations, and import the shop settings. See docs/database-setup.md.') from None
    with LOCK:
        return load_config()


@router.put('/api/shop-config')
def save_config(config: ShopConfig):
    try:
        check_config(config)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if storage_backend() == 'postgres':
        from bobaops.catalog.repository import StaleRevision, write_config
        try:
            return write_config(config)
        except StaleRevision as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except IntegrityError:
            raise HTTPException(400, 'Database constraint rejected this change. Check duplicate names, references, and quantities.') from None
        except (SQLAlchemyError, KeyError):
            raise HTTPException(503, 'Database is not ready. Start PostgreSQL and run migrations.') from None
    with LOCK:
        current = load_config()
        if current.revision != config.revision:
            raise HTTPException(409, 'Settings changed in another tab. Reload the saved settings before editing again.')
        config.revision += 1
        STORE.parent.mkdir(parents=True, exist_ok=True)
        # Preserve a previous snapshot for recovery; all files remain private.
        if STORE.exists():
            history = STORE.parent / 'shop_history'
            history.mkdir(exist_ok=True)
            (history / f'revision-{current.revision}.json').write_text(STORE.read_text())
        temp = STORE.with_suffix('.tmp')
        temp.write_text(config.model_dump_json(indent=2))
        os.replace(temp, STORE)
    return config
