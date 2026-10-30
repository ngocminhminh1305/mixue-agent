"""Catalog records and cross-record validation, without storage side effects."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=200)
    assumed: bool = True
    notes: str = Field(default='', max_length=2000)


class StockItem(Record):
    purchase_unit: str | None = Field(default=None, max_length=30)
    purchase_quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    purchase_price_vnd: int | None = Field(default=None, ge=0, strict=True)
    purchase_description: str = Field(default='', max_length=2000)
    ice_cream_powder: bool = False
    unit: Literal['g', 'ml', 'piece']
    kind: Literal['raw', 'prepared', 'packaging']
    opening_stock: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_per_unit: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Component(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str
    quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class Product(Record):
    category: Literal['fruit_tea', 'milk_tea', 'coffee', 'cone', 'topping', 'sundae']
    size: Literal['M', 'L'] | None = None
    price_vnd: int | None = Field(default=None, ge=0, strict=True)
    active: bool = True
    components: list[Component] = Field(default_factory=list)


class Preparation(Record):
    deduction_mode: Literal["automatic", "manual_ice_cream"] = "automatic"
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
    manual_outputs = {p.output_item_id for p in config.preparations if p.deduction_mode == 'manual_ice_cream'}
    for item in config.stock_items:
        if item.purchase_quantity is not None and item.purchase_price_vnd is not None:
            item.cost_per_unit = float((Decimal(item.purchase_price_vnd) / Decimal(str(item.purchase_quantity))).quantize(Decimal('0.000001')))
        if item.ice_cream_powder and (item.kind != "raw" or item.unit != "g"):
            raise ValueError("Ice-cream powder must be a purchased raw ingredient measured in grams.")
        if item.unit == 'piece' and item.opening_stock is not None and not item.opening_stock.is_integer():
            raise ValueError('Opening packaging stock must be whole pieces.')
        if item.unit == 'piece' and item.purchase_quantity is not None and not item.purchase_quantity.is_integer():
            raise ValueError('Purchase packaging quantities must be whole pieces.')
    def components(values, preparation=False):
        if len({x.item_id for x in values}) != len(values):
            raise ValueError('A recipe cannot contain the same stock item twice.')
        for part in values:
            if part.item_id not in items:
                raise ValueError('A recipe references a missing stock item. Remove its recipe references before deleting it.')
            if part.quantity is None:
                if preparation or part.item_id not in manual_outputs:
                    raise ValueError('A quantity is required except for manually tracked ice cream in product recipes.')
                continue
            if items[part.item_id].unit == 'piece' and not part.quantity.is_integer():
                raise ValueError('Packaging quantities must be whole pieces.')
    for product in config.products:
        components(product.components)
        if product.category == 'coffee' and product.size != 'M':
            raise ValueError('Coffee products must use size M.')
        if product.category in {'fruit_tea', 'milk_tea'} and product.size is None:
            raise ValueError('Cup drinks require M or L size.')
        if product.category in {'cone', 'topping', 'sundae'} and product.size is not None:
            raise ValueError('Cones and extra toppings do not have a cup size.')
    outputs = set()
    for prep in config.preparations:
        components(prep.inputs, preparation=True)
        if prep.output_item_id not in items or items[prep.output_item_id].kind != 'prepared':
            raise ValueError('Preparation output must be a prepared stock item.')
        if prep.output_item_id in outputs:
            raise ValueError('Each prepared stock item must have at most one current preparation recipe.')
        outputs.add(prep.output_item_id)
        if prep.deduction_mode == "manual_ice_cream":
            if items[prep.output_item_id].unit != "g":
                raise ValueError("Manual ice-cream usage requires a prepared output measured in grams.")
            if not any(x.ice_cream_powder for x in config.stock_items):
                raise ValueError("Mark the raw powders for daily ice-cream usage before enabling manual ice cream.")
            if any(not items[x.item_id].ice_cream_powder for x in prep.inputs):
                raise ValueError("Manual ice-cream preparation inputs must be flagged ice-cream powders. Other product ingredients stay in the product recipe.")
        if any(items[x.item_id].kind != 'raw' for x in prep.inputs):
            raise ValueError('Preparation inputs must be raw stock items.')

