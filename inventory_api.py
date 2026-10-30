"""Inventory balances and atomic, retry-safe purchase/count/waste operations."""
import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import get_engine, get_tables, storage_backend
from inventory import LIMIT, SCALE

router = APIRouter(prefix='/api/inventory')
TOKEN_PATTERN = r'^[a-f0-9]{64}$'


class Request(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID


class PurchaseLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str = Field(min_length=1, max_length=100)
    quantity: Decimal = Field(gt=0, le=LIMIT, max_digits=20, decimal_places=6, allow_inf_nan=False)
    total_cost_vnd: int | None = Field(default=None, ge=0, le=9223372036854775807, strict=True)


class Purchase(Request):
    purchased_on: date
    note: str = Field(default='', max_length=2000)
    items: list[PurchaseLine] = Field(min_length=1, max_length=500)

    @field_validator('items')
    @classmethod
    def distinct_items(cls, values):
        if len({v.item_id for v in values}) != len(values):
            raise ValueError('Include each stock item once; combine quantities for repeated items.')
        return values


class ItemFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str = Field(min_length=1, max_length=100)
    balance_token: str = Field(pattern=TOKEN_PATTERN)


class ReasonedFields(ItemFields):
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def meaningful_reason(cls, value):
        if not value.strip():
            raise ValueError('Enter a reason for the stock change.')
        return value.strip()


class CountLine(ReasonedFields):
    actual_quantity: Decimal = Field(ge=0, le=LIMIT, max_digits=20, decimal_places=6, allow_inf_nan=False)


class WasteLine(ReasonedFields):
    quantity: Decimal = Field(gt=0, le=LIMIT, max_digits=20, decimal_places=6, allow_inf_nan=False)


class ThresholdLine(ItemFields):
    threshold: Decimal | None = Field(default=None, ge=0, le=LIMIT, max_digits=20, decimal_places=6, allow_inf_nan=False)


class StockCount(CountLine, Request):
    pass


class Waste(WasteLine, Request):
    pass


class Threshold(ThresholdLine, Request):
    pass


class BatchRequest(Request):
    @field_validator('items', check_fields=False)
    @classmethod
    def distinct_items(cls, values):
        if len({v.item_id for v in values}) != len(values):
            raise ValueError('Include each stock item once per table save.')
        return values


class CountBatch(BatchRequest):
    items: list[CountLine] = Field(min_length=1, max_length=500)


class WasteBatch(BatchRequest):
    items: list[WasteLine] = Field(min_length=1, max_length=500)


class ThresholdBatch(BatchRequest):
    items: list[ThresholdLine] = Field(min_length=1, max_length=500)


def formatted(value):
    return format(Decimal(value).quantize(SCALE), 'f')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


@contextmanager
def connection(read_only=False):
    if storage_backend() != 'postgres':
        raise HTTPException(503, 'Inventory requires SHOP_STORAGE=postgres and a migrated database.')
    try:
        t = get_tables()
        if 'stock_counts' not in t or 'inventory_operations' not in t or 'low_stock_threshold' not in t['stock_items'].c:
            raise HTTPException(503, 'Run python3 -m alembic upgrade head, then restart the backend to enable Inventory.')
        isolation = 'REPEATABLE READ' if read_only else 'READ COMMITTED'
        with get_engine().connect().execution_options(isolation_level=isolation) as conn, conn.begin():
            yield conn, t
    except IntegrityError:
        raise HTTPException(409, 'A conflicting inventory entry already exists. Refresh Inventory before trying again.') from None
    except (SQLAlchemyError, KeyError):
        raise HTTPException(503, 'Inventory database is unavailable. Check PostgreSQL and migrations, then refresh.') from None


def stock_states(conn, t, ids=None):
    stock, movements, counts = t['stock_items'], t['inventory_movements'], t['stock_counts']
    query = select(stock).where(~stock.c.is_deleted, stock.c.kind != 'prepared')
    if ids is not None:
        query = query.where(stock.c.id.in_(ids))
    rows = conn.execute(query.order_by(stock.c.name, stock.c.id)).mappings().all()
    wanted = [r['id'] for r in rows]
    sums = {r['item_id']: r for r in conn.execute(
        select(movements.c.item_id, func.sum(movements.c.signed_quantity).label('balance'), func.count().label('movement_count'))
        .where(movements.c.item_id.in_(wanted)).group_by(movements.c.item_id)).mappings()}
    observations = {r['item_id']: r for r in conn.execute(
        select(counts.c.item_id, func.count().label('count_count'), func.max(counts.c.counted_at).label('last_counted_at'))
        .where(counts.c.item_id.in_(wanted)).group_by(counts.c.item_id)).mappings()}
    result = []
    for row in rows:
        total = sums.get(row['id'], {})
        seen = observations.get(row['id'], {})
        balance = total.get('balance', Decimal(0))
        known = row['opening_stock'] is not None or bool(seen.get('count_count'))
        threshold = row['low_stock_threshold']
        state = {'id': row['id'], 'name': row['name'], 'unit': row['unit'], 'kind': row['kind'],
                 'ice_cream_powder': row.get('ice_cream_powder', False),
                 'balance_known': known, 'quantity': formatted(balance) if known else None,
                 'ledger_estimate': formatted(balance), 'negative_ledger': balance < 0,
                 'low_stock_threshold': formatted(threshold) if threshold is not None else None,
                 'low_stock': known and threshold is not None and balance <= threshold,
                 'estimated_low_stock': not known and threshold is not None and balance <= threshold,
                 'last_counted_at': seen.get('last_counted_at')}
        state['balance_token'] = fingerprint({
            'id': row['id'], 'unit': row['unit'], 'kind': row['kind'], 'balance': formatted(balance), 'known': known,
            'movement_count': total.get('movement_count', 0), 'count_count': seen.get('count_count', 0),
            'threshold': state['low_stock_threshold'],
        })
        result.append(state)
    return result


def checked_stock(conn, t, ids):
    table = t['stock_items']
    records = conn.execute(select(table).where(table.c.id.in_(ids)).order_by(table.c.id).with_for_update()).mappings().all()
    if len(records) != len(set(ids)) or any(r['is_deleted'] or r['kind'] == 'prepared' for r in records):
        raise HTTPException(400, 'Choose current purchased ingredients or packaging. Prepared bases are tracked through their raw ingredients.')
    return {r['id']: r for r in records}


def validate_quantity(value, unit):
    if value is not None and unit == 'piece' and value != value.to_integral_value():
        raise HTTPException(400, 'Packaging/cone quantities and warning levels must be whole pieces.')


def checked_balance(value):
    if abs(value) > LIMIT:
        raise HTTPException(400, 'The resulting balance or adjustment exceeds supported quantity limits.')


def execute_operation(kind, body, apply):
    payload = body.model_dump(mode='json', exclude={'request_id'})
    digest = fingerprint({'kind': kind, 'payload': payload})
    identifier = str(body.request_id)
    with connection() as (conn, t):
        # Same order as catalog edits / sales approvals: catalog, then stock IDs.
        conn.execute(select(t['shop_state'].c.revision).where(t['shop_state'].c.id == 1).with_for_update(read=True)).all()
        ops = t['inventory_operations']
        created = conn.scalar(pg_insert(ops).values(id=identifier, kind=kind, request_hash=digest, payload=payload)
                              .on_conflict_do_nothing(index_elements=[ops.c.id]).returning(ops.c.id))
        if created is None:
            previous = conn.execute(select(ops).where(ops.c.id == identifier)).mappings().one()
            if previous['request_hash'] != digest:
                raise HTTPException(409, 'This request ID was already used for different data. Refresh the form.')
            return {**previous['result'], 'replayed': True}
        result = jsonable_encoder(apply(conn, t, identifier))
        result.update(operation_id=identifier, kind=kind, replayed=False)
        conn.execute(update(ops).where(ops.c.id == identifier).values(result=result))
        return result


@router.get('')
def get_inventory():
    with connection(read_only=True) as (conn, t):
        return {'items': stock_states(conn, t)}


@router.post('/purchases', status_code=201)
def record_purchase(body: Purchase):
    def apply(conn, t, identifier):
        ids = [line.item_id for line in body.items]
        stocks = checked_stock(conn, t, ids)
        before = {r['id']: r for r in stock_states(conn, t, ids)}
        now = datetime.now(timezone.utc)
        conn.execute(insert(t['purchases']).values(id=identifier, purchased_at=now, purchased_on=body.purchased_on, note=body.note))
        for line in body.items:
            validate_quantity(line.quantity, stocks[line.item_id]['unit'])
            checked_balance(Decimal(before[line.item_id]['ledger_estimate']) + line.quantity)
            line_id = uuid4().hex
            conn.execute(insert(t['purchase_items']).values(id=line_id, purchase_id=identifier, item_id=line.item_id,
                         quantity=line.quantity, total_cost_vnd=line.total_cost_vnd))
            conn.execute(insert(t['inventory_movements']).values(id=uuid4().hex, item_id=line.item_id, kind='purchase',
                         signed_quantity=line.quantity, purchase_item_id=line_id, occurred_at=now, note=body.note))
        return {'items': stock_states(conn, t, ids), 'note': body.note, 'purchased_on': body.purchased_on.isoformat()}
    return execute_operation('purchase', body, apply)


def apply_item_change(conn, t, identifier, kind, body, stock, before):
    if body.balance_token != before['balance_token']:
        raise HTTPException(409, f'{stock["name"]}: stock changed since this table was loaded. Refresh Inventory; for a physical count, recount before saving. Nothing in this save was committed.')
    if kind == 'threshold':
        validate_quantity(body.threshold, stock['unit'])
        conn.execute(update(t['stock_items']).where(t['stock_items'].c.id == body.item_id).values(low_stock_threshold=body.threshold))
        return {'items': stock_states(conn, t, [body.item_id]), 'note': 'Low-stock warning level updated.'}
    previous = Decimal(before['ledger_estimate'])
    value = body.actual_quantity if kind == 'count' else body.quantity
    validate_quantity(value, stock['unit'])
    delta = value - previous if kind == 'count' else -value
    checked_balance(delta)
    checked_balance(previous + delta)
    movement_id = uuid4().hex if delta else None
    now = datetime.now(timezone.utc)
    if movement_id:
        conn.execute(insert(t['inventory_movements']).values(id=movement_id, item_id=body.item_id,
                     kind='correction' if kind == 'count' else 'waste', signed_quantity=delta, note=body.reason, occurred_at=now))
    if kind == 'count':
        conn.execute(insert(t['stock_counts']).values(id=identifier, item_id=body.item_id,
                     counted_quantity=value, ledger_balance_before=previous, adjustment_quantity=delta,
                     movement_id=movement_id, counted_at=now))
    return {'items': stock_states(conn, t, [body.item_id]), 'note': body.reason,
            'balance_before': before['quantity'], 'ledger_before': before['ledger_estimate'],
            'adjustment': formatted(delta), 'actual_quantity': formatted(value) if kind == 'count' else None}


def item_change(kind, body):
    def apply(conn, t, identifier):
        stocks = checked_stock(conn, t, [body.item_id])
        before = stock_states(conn, t, [body.item_id])[0]
        return apply_item_change(conn, t, identifier, kind, body, stocks[body.item_id], before)
    return execute_operation(kind, body, apply)


def batch_change(kind, body):
    def apply(conn, t, identifier):
        ids = [line.item_id for line in body.items]
        stocks = checked_stock(conn, t, ids)
        before = {r['id']: r for r in stock_states(conn, t, ids)}
        for line in body.items:
            child_id = str(uuid5(UUID(identifier), 'count:' + line.item_id)) if kind == 'count' else identifier
            # Existing counts have a one-to-one operation link. Keep a child audit
            # record per observed item while the parent guarantees atomic retries.
            if kind == 'count':
                payload = {**line.model_dump(mode='json'), 'batch_id': identifier}
                conn.execute(insert(t['inventory_operations']).values(id=child_id, kind=kind,
                             request_hash=fingerprint({'kind': kind, 'payload': payload}), payload=payload))
            result = apply_item_change(conn, t, child_id, kind, line, stocks[line.item_id], before[line.item_id])
            if kind == 'count':
                conn.execute(update(t['inventory_operations']).where(t['inventory_operations'].c.id == child_id)
                             .values(result=jsonable_encoder({**result, 'operation_id': child_id, 'kind': kind, 'replayed': False})))
        return {'items': stock_states(conn, t, ids), 'entry_count': len(ids), 'note': f'{len(ids)} inventory rows saved together.'}
    return execute_operation(kind, body, apply)


@router.post('/counts', status_code=201)
def record_count(body: StockCount):
    return item_change('count', body)


@router.post('/waste', status_code=201)
def record_waste(body: Waste):
    return item_change('waste', body)


@router.post('/thresholds')
def set_threshold(body: Threshold):
    return item_change('threshold', body)


@router.post('/counts/batch', status_code=201)
def record_counts(body: CountBatch):
    return batch_change('count', body)


@router.post('/waste/batch', status_code=201)
def record_waste_batch(body: WasteBatch):
    return batch_change('waste', body)


@router.post('/thresholds/batch')
def set_thresholds(body: ThresholdBatch):
    return batch_change('threshold', body)


@router.get('/history')
def get_history(item_id: str | None = Query(default=None, max_length=100)):
    with connection(read_only=True) as (conn, t):
        m, s, sale, purchase = t['inventory_movements'], t['stock_items'], t['sales_items'], t['purchase_items']
        original = m.alias('original_sale_movement')
        original_sale = sale.alias('original_sale_item')
        query = select(m.c.id, m.c.item_id, s.c.name, s.c.unit, m.c.kind, m.c.signed_quantity, m.c.occurred_at, m.c.note, m.c.reversal_of,
                       func.coalesce(sale.c.report_id, original_sale.c.report_id).label('report_id'),
                       purchase.c.purchase_id, purchase.c.total_cost_vnd).select_from(
                           m.join(s, m.c.item_id == s.c.id).outerjoin(sale, m.c.sale_item_id == sale.c.id)
                           .outerjoin(original, m.c.reversal_of == original.c.id)
                           .outerjoin(original_sale, original.c.sale_item_id == original_sale.c.id)
                           .outerjoin(purchase, m.c.purchase_item_id == purchase.c.id))
        if item_id:
            query = query.where(m.c.item_id == item_id)
        movements = []
        for row in conn.execute(query.order_by(m.c.occurred_at.desc(), m.c.id.desc()).limit(100)).mappings():
            movements.append({**dict(row), 'signed_quantity': formatted(row['signed_quantity'])})
        # Counts without a delta still have a historical record, even though zero
        # movements are deliberately excluded from the stock ledger.
        c = t['stock_counts']
        ops = t['inventory_operations']
        counts_query = select(c, s.c.name, s.c.unit, ops.c.payload.label('entry')).select_from(
            c.join(s, c.c.item_id == s.c.id).join(ops, c.c.id == ops.c.id))
        if item_id:
            counts_query = counts_query.where(c.c.item_id == item_id)
        counts = [{**{key: value for key, value in row.items() if key != 'entry'}, 'reason': row['entry'].get('reason', ''),
                   'counted_quantity': formatted(row['counted_quantity']),
                   'ledger_balance_before': formatted(row['ledger_balance_before']),
                   'adjustment_quantity': formatted(row['adjustment_quantity'])}
                  for row in conn.execute(counts_query.order_by(c.c.counted_at.desc(), c.c.id.desc()).limit(30)).mappings()]
        return {'movements': movements, 'counts': counts}
