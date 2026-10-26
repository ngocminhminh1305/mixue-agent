"""Reverse recorded sale deductions once, retaining the original report and photos."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, update

from inventory_api import checked_balance, fingerprint, formatted
from sales import connection, details

router = APIRouter(prefix='/api/sales')


class ReverseReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    revision: int = Field(ge=0, strict=True)
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=1, max_length=2000)
    confirm_stock_impact: bool = Field(default=False, strict=True)
    create_correction_draft: bool = Field(default=True, strict=True)


def approved_report(conn, tables, identifier):
    table = tables['sales_reports']
    report = conn.execute(select(table).where(table.c.id == identifier).with_for_update()).mappings().first()
    if not report:
        raise HTTPException(404, 'Sales report not found.')
    return report


def lock_inventory(conn, t):
    # Match approval/catalog/inventory lock order: report, shared shop state, stocks by ID.
    conn.execute(select(t['shop_state'].c.id).where(t['shop_state'].c.id == 1).with_for_update(read=True)).all()
    conn.execute(select(t['stock_items'].c.id).order_by(t['stock_items'].c.id).with_for_update()).all()


def preview(conn, t, report):
    if report['status'] != 'approved':
        raise HTTPException(409, 'Only an approved report can be reversed.')
    movements, items = t['inventory_movements'], t['sales_items']
    originals = conn.execute(select(movements).join(items, movements.c.sale_item_id == items.c.id)
                            .where(items.c.report_id == report['id'], movements.c.kind == 'sale')
                            .order_by(movements.c.id)).mappings().all()
    ids = [m['id'] for m in originals]
    if conn.scalar(select(movements.c.id).where(movements.c.reversal_of.in_(ids)).limit(1)):
        raise HTTPException(409, 'Some deductions already have reversals. Check stock history before continuing.')
    totals = defaultdict(Decimal)
    for original in originals:
        totals[original['item_id']] -= original['signed_quantity']
    stock = {r['id']: r for r in conn.execute(select(t['stock_items']).where(t['stock_items'].c.id.in_(totals))).mappings()}
    balances = {r['item_id']: r for r in conn.execute(select(movements.c.item_id,
                func.sum(movements.c.signed_quantity).label('balance'), func.count().label('movement_count'))
                .where(movements.c.item_id.in_(totals)).group_by(movements.c.item_id)).mappings()}
    counts = t['stock_counts']
    observations = {r['item_id']: r for r in conn.execute(select(counts.c.item_id,
                    func.max(counts.c.counted_at).label('latest'), func.count().label('count_count'))
                    .where(counts.c.item_id.in_(totals)).group_by(counts.c.item_id)).mappings()}
    restored, warnings = [], []
    for identifier, quantity in sorted(totals.items()):
        item, ledger, observed = stock[identifier], balances.get(identifier, {}), observations.get(identifier, {})
        before = ledger.get('balance', Decimal(0))
        after = before + quantity
        checked_balance(after)
        known = item['opening_stock'] is not None or bool(observed.get('count_count'))
        counted_after = bool(observed.get('latest') and observed['latest'] >= report['approved_at'])
        if counted_after:
            warnings.append(f'{item["name"]}: physically counted after approval. Reversal changes the current ledger; recount this item after correcting the report.')
        restored.append({'item_id': identifier, 'name': item['name'], 'unit': item['unit'],
                         'quantity': formatted(quantity), 'balance_known': known,
                         'ledger_before': formatted(before), 'ledger_after': formatted(after),
                         'balance_before': formatted(before) if known else None,
                         'balance_after': formatted(after) if known else None,
                         'counted_after_approval': counted_after,
                         'latest_count_at': observed['latest'].isoformat() if observed.get('latest') else None,
                         'movement_count': ledger.get('movement_count', 0), 'count_count': observed.get('count_count', 0),
                         'archived_item': item['is_deleted']})
    if not originals:
        warnings.append('This report has no recorded stock deductions. Reversal changes sales status only; no stock will be added.')
    plan = {'report_id': report['id'], 'date': report['report_date'].isoformat(), 'revision': report['revision'],
            'restored_items': restored, 'warnings': warnings, 'movement_count': len(originals),
            'movements': [{'id': m['id'], 'item_id': m['item_id'], 'quantity': formatted(-m['signed_quantity'])} for m in originals]}
    plan['preview_token'] = fingerprint(plan)
    return plan


def copy_draft(conn, t, report):
    """Reuse immutable photo files, with separate row/image identities for approval."""
    new_id = uuid4().hex
    conn.execute(insert(t['sales_reports']).values(id=new_id, report_date=report['report_date'], issues=report['issues']))
    image_map = {}
    for image in conn.execute(select(t['sales_images']).where(t['sales_images'].c.report_id == report['id'])).mappings().all():
        identifier = uuid4().hex
        image_map[image['id']] = identifier
        conn.execute(insert(t['sales_images']).values(id=identifier, report_id=new_id,
                     content_sha256=image['content_sha256'], source_path=image['source_path'], extraction=image['extraction']))
    for row in conn.execute(select(t['sales_items']).where(t['sales_items'].c.report_id == report['id'])
                            .order_by(t['sales_items'].c.id)).mappings().all():
        conn.execute(insert(t['sales_items']).values(id=uuid4().hex, report_id=new_id,
                     product_id=row['product_id'], raw_item_name=row['raw_item_name'], quantity=row['quantity'],
                     issues=row['issues'], source_image_id=image_map.get(row['source_image_id'])))
    return new_id


def result(conn, t, reversal, already_applied=False):
    correction = reversal['replacement_report_id']
    return {'report': details(conn, t, reversal['report_id']),
            'correction_draft': details(conn, t, correction) if correction else None,
            'reversal': dict(reversal), 'already_applied': already_applied}


@router.get('/reports/{identifier}/reversal-preview')
def preview_reversal(identifier: str):
    with connection() as (conn, tables):
        report = approved_report(conn, tables, identifier)
        lock_inventory(conn, tables)
        return preview(conn, tables, report)


@router.post('/reports/{identifier}/reverse')
def reverse_report(identifier: str, body: ReverseReport):
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, 'Enter a reason for reversing this report.')
    request_id = str(body.request_id)
    request_hash = fingerprint({'report_id': identifier, **body.model_dump(mode='json', exclude={'request_id'})})
    with connection() as (conn, tables):
        report = approved_report(conn, tables, identifier)
        table = tables['sales_reversals']
        previous = conn.execute(select(table).where(table.c.id == request_id)).mappings().first()
        if previous:
            if previous['request_hash'] != request_hash:
                raise HTTPException(409, 'This request ID was already used with different reversal details.')
            return result(conn, tables, previous, already_applied=True)
        if report['status'] != 'approved':
            raise HTTPException(409, 'This report is not approved or has already been reversed. No stock was added again.')
        if report['revision'] != body.revision:
            raise HTTPException(409, 'The report changed. Reload it and preview the reversal again.')
        lock_inventory(conn, tables)
        plan = preview(conn, tables, report)
        if plan['preview_token'] != body.preview_token:
            raise HTTPException(409, 'Stock or report details changed since the preview. Preview the reversal again.')
        if not body.confirm_stock_impact:
            raise HTTPException(400, 'Confirm the stock changes and any physical-count warnings before reversing.')
        now = datetime.now(timezone.utc)
        for original in plan['movements']:
            conn.execute(insert(tables['inventory_movements']).values(id=uuid4().hex, item_id=original['item_id'],
                         kind='reversal', signed_quantity=Decimal(original['quantity']), occurred_at=now,
                         reversal_of=original['id'], note=f'Reverse sales {report["report_date"]} ({identifier}): {reason}'))
        conn.execute(update(tables['sales_reports']).where(tables['sales_reports'].c.id == identifier)
                     .values(status='reversed', revision=report['revision'] + 1))
        correction = copy_draft(conn, tables, report) if body.create_correction_draft else None
        saved = {'id': request_id, 'report_id': identifier, 'report_revision': body.revision,
                 'request_hash': request_hash, 'reason': reason, 'reversed_at': now,
                 'replacement_report_id': correction, 'snapshot': plan}
        conn.execute(insert(table).values(**saved))
        return result(conn, tables, saved)
