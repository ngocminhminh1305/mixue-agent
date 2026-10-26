"""Validated shop settings with PostgreSQL and an explicit JSON prototype mode."""
import json
import os
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from database import storage_backend

STORE = Path(__file__).resolve().parent / 'data/private/shop_config.json'
LOCK = threading.Lock()
router = APIRouter()


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=200)
    assumed: bool = True
    notes: str = Field(default='', max_length=2000)


class StockItem(Record):
    unit: Literal['g', 'ml', 'piece']
    kind: Literal['raw', 'prepared', 'packaging']
    opening_stock: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_per_unit: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Component(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str
    quantity: float = Field(gt=0, allow_inf_nan=False)


class Product(Record):
    category: Literal['fruit_tea', 'milk_tea', 'coffee', 'cone', 'topping']
    size: Literal['M', 'L'] | None = None
    price_vnd: int | None = Field(default=None, ge=0, strict=True)
    active: bool = True
    components: list[Component] = Field(default_factory=list)


class Preparation(Record):
    output_item_id: str
    output_quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    inputs: list[Component] = Field(default_factory=list)


class ShopConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    stock_items: list[StockItem]
    products: list[Product]
    preparations: list[Preparation]
    questions: list[str]


def check_config(config):
    for records in (config.stock_items, config.products, config.preparations):
        if len({x.id for x in records}) != len(records):
            raise ValueError('Record IDs must be unique within each section.')
        if len({x.name.strip().casefold() for x in records}) != len(records):
            raise ValueError('Names must be unique within each section, including product size.')
        if any(not x.name.strip() for x in records):
            raise ValueError('Names cannot be blank.')
    items = {x.id: x for x in config.stock_items}
    for item in config.stock_items:
        if item.unit == 'piece' and item.opening_stock is not None and not item.opening_stock.is_integer():
            raise ValueError('Opening packaging stock must be whole pieces.')
    def components(values):
        if len({x.item_id for x in values}) != len(values):
            raise ValueError('A recipe cannot contain the same stock item twice.')
        for part in values:
            if part.item_id not in items:
                raise ValueError('A recipe references a missing stock item. Remove its recipe references before deleting it.')
            if items[part.item_id].unit == 'piece' and not part.quantity.is_integer():
                raise ValueError('Packaging quantities must be whole pieces.')
    for product in config.products:
        components(product.components)
        if product.category == 'coffee' and product.size != 'M':
            raise ValueError('Coffee products must use size M.')
        if product.category in {'fruit_tea', 'milk_tea'} and product.size is None:
            raise ValueError('Cup drinks require M or L size.')
        if product.category in {'cone', 'topping'} and product.size is not None:
            raise ValueError('Cones and extra toppings do not have a cup size.')
    outputs = set()
    for prep in config.preparations:
        components(prep.inputs)
        if prep.output_item_id not in items or items[prep.output_item_id].kind != 'prepared':
            raise ValueError('Preparation output must be a prepared stock item.')
        if prep.output_item_id in outputs:
            raise ValueError('Each prepared stock item must have at most one current preparation recipe.')
        outputs.add(prep.output_item_id)
        if any(items[x.item_id].kind != 'raw' for x in prep.inputs):
            raise ValueError('Preparation inputs must be raw stock items.')


def load_config():
    if not STORE.exists():
        return ShopConfig(revision=0, stock_items=[], products=[], preparations=[], questions=[])
    return ShopConfig.model_validate_json(STORE.read_text())


@router.get('/api/shop-config')
def get_config():
    if storage_backend() == 'postgres':
        from catalog_repository import read_config
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
        from catalog_repository import StaleRevision, write_config
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
