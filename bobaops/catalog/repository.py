"""Transactional PostgreSQL persistence for the existing shop-setup API."""
from decimal import Decimal
from contextlib import nullcontext
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from bobaops.core.database import get_engine, get_tables


class StaleRevision(ValueError):
    pass


def numeric(value):
    return None if value is None else Decimal(str(value))


def latest(conn, table, column, owner):
    return conn.execute(select(table).where(column == owner).order_by(table.c.version.desc()).limit(1)).mappings().first()


def read_config():
    from bobaops.catalog.schemas import ShopConfig
    t = get_tables()
    # Repeatable-read gives a consistent snapshot across these independent tables.
    with get_engine().connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        revision = conn.scalar(select(t['shop_state'].c.revision).where(t['shop_state'].c.id == 1))
        stocks = []
        for row in conn.execute(select(t['stock_items']).where(~t['stock_items'].c.is_deleted).order_by(t['stock_items'].c.id)).mappings():
            stocks.append({k: row[k] for k in ('id', 'name', 'unit', 'kind', 'opening_stock', 'cost_per_unit', 'assumed', 'notes', 'ice_cream_powder', 'purchase_unit', 'purchase_quantity', 'purchase_price_vnd', 'purchase_description')})
        products = []
        for row in conn.execute(select(t['products']).where(~t['products'].c.is_deleted).order_by(t['products'].c.id)).mappings():
            product = {k: row[k] for k in ('id', 'name', 'category', 'size', 'active', 'assumed', 'notes')}
            recipe = latest(conn, t['recipe_versions'], t['recipe_versions'].c.product_id, row['id'])
            price = latest(conn, t['price_versions'], t['price_versions'].c.product_id, row['id'])
            product['price_vnd'] = price['price_vnd'] if price else None
            product['components'] = [] if recipe is None else [dict(x) for x in conn.execute(
                select(t['recipe_components'].c.item_id, t['recipe_components'].c.quantity)
                .where(t['recipe_components'].c.recipe_id == recipe['id']).order_by(t['recipe_components'].c.item_id)).mappings()]
            products.append(product)
        preparations = []
        for row in conn.execute(select(t['preparations']).where(~t['preparations'].c.is_deleted).order_by(t['preparations'].c.id)).mappings():
            prep = {k: row[k] for k in ('id', 'name', 'output_item_id', 'assumed', 'notes')}
            version = latest(conn, t['preparation_versions'], t['preparation_versions'].c.preparation_id, row['id'])
            prep['output_quantity'] = version['output_quantity'] if version else None
            prep['deduction_mode'] = version['deduction_mode'] if version else 'automatic'
            prep['inputs'] = [] if version is None else [dict(x) for x in conn.execute(
                select(t['preparation_inputs'].c.item_id, t['preparation_inputs'].c.quantity)
                .where(t['preparation_inputs'].c.preparation_version_id == version['id']).order_by(t['preparation_inputs'].c.item_id)).mappings()]
            preparations.append(prep)
        questions = list(conn.scalars(select(t['saved_questions'].c.question).order_by(t['saved_questions'].c.position)))
    return ShopConfig(revision=revision, stock_items=stocks, products=products, preparations=preparations, questions=questions)


def sync_records(conn, table, values):
    existing = {x['id']: x for x in conn.execute(select(table)).mappings()}
    # Archive first, including the records about to change names. This permits renames/swaps
    # while keeping historical foreign keys intact and enforcing current-name uniqueness.
    conn.execute(update(table).values(is_deleted=True))
    for row in values:
        row = {**row, 'is_deleted': False}
        if row['id'] in existing:
            conn.execute(update(table).where(table.c.id == row['id']).values(**row))
        else:
            conn.execute(insert(table).values(**row))
    return existing


def part_map(values):
    return {x.item_id: numeric(x.quantity) for x in values}


def stored_parts(conn, table, fk, owner):
    return {x['item_id']: x['quantity'] for x in conn.execute(select(table).where(fk == owner)).mappings()}


def write_config(config, initial_import=False, *, connection=None):
    from bobaops.catalog.schemas import check_config
    check_config(config)
    t = get_tables()
    with (get_engine().begin() if connection is None else nullcontext(connection)) as conn:
        state = conn.execute(select(t['shop_state']).where(t['shop_state'].c.id == 1).with_for_update()).mappings().one()
        if initial_import:
            if state['revision'] != 0 or conn.scalar(select(t['stock_items'].c.id).limit(1)) is not None:
                raise ValueError('Database already contains setup data. Import refused to prevent overwriting edits.')
        elif state['revision'] != config.revision:
            raise StaleRevision('Settings changed in another tab. Reload before editing again.')
        new_revision = state['revision'] + 1
        old_stock = {x['id']: x for x in conn.execute(select(t['stock_items'])).mappings()}
        counted_items = set(conn.scalars(select(t['stock_counts'].c.item_id).distinct())) if 'stock_counts' in t else set()
        for item in config.stock_items:
            old = old_stock.get(item.id)
            if old and (item.unit != old['unit'] or item.kind != old['kind']):
                raise ValueError('Stock unit and type cannot change after database import. Add a new stock item and update the recipes instead.')
            if old and item.id in counted_items and numeric(item.opening_stock) != old['opening_stock']:
                raise ValueError('This item has a physical count. Use the Inventory page to correct current stock instead of changing its opening baseline.')
            if item.kind == 'prepared' and item.opening_stock is not None:
                raise ValueError('Enter opening stock for purchased powders, not prepared bases; otherwise stock could be counted twice.')
        stock_values = []
        for item in config.stock_items:
            row = item.model_dump()
            row.update(opening_stock=numeric(item.opening_stock), cost_per_unit=numeric(item.cost_per_unit), purchase_quantity=numeric(item.purchase_quantity))
            stock_values.append(row)
        sync_records(conn, t['stock_items'], stock_values)
        for item in config.stock_items:
            old = old_stock.get(item.id)
            old_amount = (old['opening_stock'] if old else None) or Decimal(0)
            new_amount = numeric(item.opening_stock) or Decimal(0)
            delta = new_amount - old_amount
            if delta:
                conn.execute(insert(t['inventory_movements']).values(
                    id=uuid4().hex, item_id=item.id,
                    kind='opening' if not old or old['opening_stock'] is None else 'opening_adjustment',
                    signed_quantity=delta, note=f'Opening stock edited in setup revision {new_revision}'))
        sync_records(conn, t['products'], [{k: v for k, v in x.model_dump().items() if k not in {'components', 'price_vnd'}} for x in config.products])
        for product in config.products:
            recipe = latest(conn, t['recipe_versions'], t['recipe_versions'].c.product_id, product.id)
            changed = recipe is None or recipe['assumed'] != product.assumed or recipe['notes'] != product.notes
            if recipe is not None:
                changed |= part_map(product.components) != stored_parts(conn, t['recipe_components'], t['recipe_components'].c.recipe_id, recipe['id'])
            if changed:
                version = 1 if recipe is None else recipe['version'] + 1
                identifier = uuid4().hex
                conn.execute(insert(t['recipe_versions']).values(id=identifier, product_id=product.id, version=version, assumed=product.assumed, notes=product.notes))
                for part in product.components:
                    conn.execute(insert(t['recipe_components']).values(recipe_id=identifier, item_id=part.item_id, quantity=numeric(part.quantity)))
            price = latest(conn, t['price_versions'], t['price_versions'].c.product_id, product.id)
            if price is None or price['price_vnd'] != product.price_vnd or price['assumed'] != product.assumed:
                conn.execute(insert(t['price_versions']).values(id=uuid4().hex, product_id=product.id,
                    version=1 if price is None else price['version'] + 1, price_vnd=product.price_vnd, assumed=product.assumed))
        old_prep = {x['id']: x for x in conn.execute(select(t['preparations'])).mappings()}
        for prep in config.preparations:
            if prep.id in old_prep and prep.output_item_id != old_prep[prep.id]['output_item_id']:
                raise ValueError('A saved preparation must keep its output stock item. Add a new preparation instead.')
        sync_records(conn, t['preparations'], [{k: v for k, v in x.model_dump().items() if k not in {'inputs', 'output_quantity', 'deduction_mode'}} for x in config.preparations])
        for prep in config.preparations:
            old = latest(conn, t['preparation_versions'], t['preparation_versions'].c.preparation_id, prep.id)
            changed = old is None or old['deduction_mode'] != prep.deduction_mode or old['output_quantity'] != numeric(prep.output_quantity) or old['assumed'] != prep.assumed or old['notes'] != prep.notes
            if old is not None:
                changed |= part_map(prep.inputs) != stored_parts(conn, t['preparation_inputs'], t['preparation_inputs'].c.preparation_version_id, old['id'])
            if changed:
                identifier = uuid4().hex
                conn.execute(insert(t['preparation_versions']).values(id=identifier, preparation_id=prep.id,
                    version=1 if old is None else old['version'] + 1, output_quantity=numeric(prep.output_quantity), deduction_mode=prep.deduction_mode, assumed=prep.assumed, notes=prep.notes))
                for part in prep.inputs:
                    conn.execute(insert(t['preparation_inputs']).values(preparation_version_id=identifier, item_id=part.item_id, quantity=numeric(part.quantity)))
        conn.execute(delete(t['saved_questions']))
        for position, question in enumerate(config.questions):
            conn.execute(insert(t['saved_questions']).values(position=position, question=question))
        saved = config.model_copy(deep=True)
        saved.revision = new_revision
        conn.execute(insert(t['config_history']).values(revision=new_revision, payload=saved.model_dump(mode='json')))
        conn.execute(update(t['shop_state']).where(t['shop_state'].c.id == 1).values(revision=new_revision))
    return saved
