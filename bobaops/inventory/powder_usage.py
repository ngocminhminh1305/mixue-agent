"""Actual powder usage for ice cream, stored as editable daily totals."""
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bobaops.inventory.routes import (BatchRequest, ItemFields, LIMIT, TOKEN_PATTERN, checked_balance,
                           checked_stock, connection, execute_operation, fingerprint, formatted, stock_states)

router = APIRouter(prefix='/api/inventory/ice-cream-usage')


class UsageLine(ItemFields):
    usage_token: str = Field(pattern=TOKEN_PATTERN)
    total_used: Decimal = Field(ge=0, le=LIMIT, max_digits=20, decimal_places=6, allow_inf_nan=False)


class UsageRequest(BatchRequest):
    used_on: date
    note: str = Field(min_length=1, max_length=2000)
    items: list[UsageLine] = Field(min_length=1, max_length=100)


def require_schema(t):
    if 'ice_cream_daily_usage' not in t:
        raise HTTPException(503, 'Run migrations and restart the backend to record ice-cream powder usage.')


def automatic_ice_cream_versions(t):
    """Track output identity even when a preparation is archived and recreated."""
    version, prep = t['preparation_versions'], t['preparations']
    manual_outputs = select(prep.c.output_item_id).select_from(
        prep.join(version, version.c.preparation_id == prep.c.id)
    ).where(version.c.deduction_mode == 'manual_ice_cream').distinct()
    lineage = select(prep.c.id).where(prep.c.output_item_id.in_(manual_outputs))
    return select(version.c.id).where(version.c.preparation_id.in_(lineage), version.c.deduction_mode == 'automatic')


def approved_automatic_usage(conn, t, day):
    report, row, link = t['sales_reports'], t['sales_items'], t['sales_preparation_versions']
    return conn.scalar(select(link.c.sales_item_id).select_from(
        link.join(row, link.c.sales_item_id == row.c.id).join(report, row.c.report_id == report.c.id))
        .where(report.c.report_date == day, report.c.status == 'approved',
               link.c.preparation_version_id.in_(automatic_ice_cream_versions(t))).limit(1)) is not None


def approval_conflict(conn, t, day, preparation_version_ids):
    usage = t['ice_cream_daily_usage']
    if not conn.scalar(select(usage.c.item_id).where(usage.c.used_on == day, usage.c.quantity > 0).limit(1)):
        return None
    if conn.scalar(select(t['preparation_versions'].c.id).where(
            t['preparation_versions'].c.id.in_(preparation_version_ids),
            t['preparation_versions'].c.id.in_(automatic_ice_cream_versions(t))).limit(1)):
        return 'Actual ice-cream powder usage already exists for this date. Use manual ice-cream preparations before approval; otherwise powders would be deducted twice.'
    return None


def daily_rows(conn, t, day, ids=None):
    require_schema(t)
    stock = t['stock_items']
    query = select(stock.c.id).where(~stock.c.is_deleted, stock.c.ice_cream_powder)
    if ids is not None:
        query = query.where(stock.c.id.in_(ids))
    wanted = list(conn.scalars(query))
    entries = {r['item_id']: r for r in conn.execute(select(t['ice_cream_daily_usage'])
               .where(t['ice_cream_daily_usage'].c.used_on == day,
                      t['ice_cream_daily_usage'].c.item_id.in_(wanted))).mappings()}
    rows = []
    for row in stock_states(conn, t, wanted):
        previous = entries.get(row['id'])
        quantity = formatted(previous['quantity'] if previous else 0)
        rows.append({**row, 'recorded': previous is not None, 'total_used': quantity, 'usage_token': fingerprint({
            'day': day.isoformat(), 'item_id': row['id'], 'quantity': quantity,
            'operation_id': previous['operation_id'] if previous else None})})
    return rows


@router.get('')
def get_daily_usage(used_on: date):
    with connection(read_only=True) as (conn, t):
        return {'used_on': used_on, 'items': daily_rows(conn, t, used_on),
                'legacy_automatic_sales': approved_automatic_usage(conn, t, used_on)}


def apply_daily_usage(conn, t, identifier, body):
    if not body.note.strip():
        raise HTTPException(422, 'Enter a note for the daily powder usage or correction.')
    require_schema(t)
    ids = [r.item_id for r in body.items]
    stocks = checked_stock(conn, t, ids)
    if any(not s['ice_cream_powder'] or s['unit'] != 'g' for s in stocks.values()):
        raise HTTPException(400, 'Choose only raw powders flagged for ice-cream usage in Shop setup.')
    if any(r.total_used > 0 for r in body.items) and approved_automatic_usage(conn, t, body.used_on):
        raise HTTPException(409, 'This date already has an approved report with automatic ice-cream deductions. Reverse it and approve a correction with manual ice cream before entering actual powders.')
    before = {r['id']: r for r in daily_rows(conn, t, body.used_on, ids)}
    now = datetime.now(timezone.utc)
    changes = []
    for line in body.items:
        previous = before[line.item_id]
        if line.balance_token != previous['balance_token'] or line.usage_token != previous['usage_token']:
            raise HTTPException(409, 'Stock or this date\'s powder total changed. Reload the powder table before saving. Nothing in this save was committed.')
        old_total = Decimal(previous['total_used'])
        delta = old_total - line.total_used
        checked_balance(delta)
        checked_balance(Decimal(previous['ledger_estimate']) + delta)
        if delta:
            conn.execute(insert(t['inventory_movements']).values(id=identifier + ':' + str(len(changes)),
                item_id=line.item_id, kind='ice_cream_usage', signed_quantity=delta, occurred_at=now,
                note=f'Ice cream {body.used_on}: daily total {formatted(old_total)} → {formatted(line.total_used)} g. {body.note.strip()}'))
        table = t['ice_cream_daily_usage']
        conn.execute(pg_insert(table).values(used_on=body.used_on, item_id=line.item_id,
                     quantity=line.total_used, operation_id=identifier, updated_at=now)
                     .on_conflict_do_update(index_elements=[table.c.used_on, table.c.item_id],
                     set_={'quantity': line.total_used, 'operation_id': identifier, 'updated_at': now}))
        changes.append({'item_id': line.item_id, 'before': formatted(old_total),
                        'after': formatted(line.total_used), 'stock_change': formatted(delta)})
    return {'used_on': body.used_on.isoformat(), 'items': daily_rows(conn, t, body.used_on, ids),
            'changes': changes, 'note': body.note.strip()}


@router.post('')
def save_daily_usage(body: UsageRequest):
    return execute_operation('ice_cream_usage', body, lambda conn, t, identifier: apply_daily_usage(conn, t, identifier, body))
