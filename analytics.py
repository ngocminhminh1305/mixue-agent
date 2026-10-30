"""Read-only dashboard metrics shared by the frontend and future assistants."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException
from sqlalchemy import Date, case, func, select

from database import environment
from inventory_api import connection, formatted, stock_states

router = APIRouter(prefix='/api/analytics')
DRINK_CATEGORIES = {'fruit_tea', 'milk_tea', 'coffee'}
CATEGORIES = DRINK_CATEGORIES | {'cone', 'topping', 'sundae'}


def bucket():
    return {'quantity': 0, 'drinks': 0, 'cones': 0, 'toppings': 0, 'sundaes': 0, 'uncategorized': 0,
            'known_price_revenue_vnd': 0, 'confirmed_price_revenue_vnd': 0,
            'assumed_price_revenue_vnd': 0, 'unverified_price_revenue_vnd': 0,
            'priced_units': 0, 'missing_price_units': 0, 'missing_price_rows': 0,
            'valid_rows': 0, 'invalid_quantity_rows': 0}


def add_sale(target, category, quantity, price, assumed):
    if quantity is None or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        target['invalid_quantity_rows'] += 1
        return
    target['valid_rows'] += 1
    target['quantity'] += quantity
    key = 'drinks' if category in DRINK_CATEGORIES else 'cones' if category == 'cone' else 'toppings' if category == 'topping' else 'sundaes' if category == 'sundae' else 'uncategorized'
    target[key] += quantity
    if price is None:
        if quantity:
            target['missing_price_units'] += quantity
            target['missing_price_rows'] += 1
        return
    revenue = quantity * int(price)
    target['priced_units'] += quantity
    target['known_price_revenue_vnd'] += revenue
    key = 'assumed_price_revenue_vnd' if assumed is True else 'confirmed_price_revenue_vnd' if assumed is False else 'unverified_price_revenue_vnd'
    target[key] += revenue


def finished(target, has_report=True):
    result = dict(target)
    no_readable_quantities = not result['valid_rows'] and result['invalid_quantity_rows'] > 0
    result['revenue_vnd'] = None if (not has_report or no_readable_quantities or
                                            (result['missing_price_units'] and not result['priced_units'])) else result['known_price_revenue_vnd']
    result['revenue_is_partial'] = bool(result['missing_price_rows'] or result['invalid_quantity_rows'])
    result['revenue_is_provisional'] = result['assumed_price_revenue_vnd'] > 0
    if not has_report or no_readable_quantities:
        for key in ('quantity', 'drinks', 'cones', 'toppings', 'sundaes', 'uncategorized'):
            result[key] = None
    return result


def summarize_sales(reports, sales, date_from, date_to):
    """Aggregate approved frozen sales, preserving missing dates and missing prices."""
    reports = [r for r in reports if date_from <= r['report_date'] <= date_to]
    by_id = {r['id']: r for r in reports}
    by_day = {}
    for report in reports:
        # A reversed original and its active correction can share the same sales date.
        if report['report_date'] not in by_day or report['status'] != 'reversed':
            by_day[report['report_date']] = report
    snapshots = {r['id']: {row['sale_item_id']: row for row in (r.get('approval_snapshot') or {}).get('rows', [])}
                 for r in reports}
    total, per_day, ranked = bucket(), defaultdict(bucket), {}
    warnings = []
    legacy_classification_rows = 0
    row_counts = defaultdict(int)
    for sale in sales:
        report = by_id.get(sale['report_id'])
        if not report or report['status'] != 'approved':
            continue
        row_counts[report['id']] += 1
        frozen = snapshots[report['id']].get(sale['id'], {})
        category = frozen.get('product_category')
        if category not in CATEGORIES:
            category = sale.get('current_category')
            legacy_classification_rows += 1
        if category not in CATEGORIES:
            category = 'uncategorized'
        name = frozen.get('product_name') or sale.get('raw_item_name') or sale.get('current_name') or 'Unknown item'
        assumed = frozen.get('unit_price_assumed', sale.get('price_assumed'))
        quantity, price = sale['quantity'], sale['unit_price_vnd']
        # The saved unit price is authoritative. Never substitute today's menu price.
        day = report['report_date']
        product_key = (sale.get('product_id') or 'unmatched:' + name, category)
        if product_key not in ranked:
            ranked[product_key] = {**bucket(), 'product_id': sale.get('product_id'), 'name': name,
                                   'category': category, 'latest_sale_date': day}
        product = ranked[product_key]
        if day >= product['latest_sale_date']:
            product['name'], product['latest_sale_date'] = name, day
        for target in (total, per_day[day], product):
            add_sale(target, category, quantity, price, assumed)
    approved = [r for r in reports if r['status'] == 'approved']
    empty_reports = {r['id'] for r in approved if not row_counts[r['id']]}
    daily, missing = [], []
    for offset in range((date_to - date_from).days + 1):
        day = date_from + timedelta(days=offset)
        report = by_day.get(day)
        status = report['status'] if report else 'missing'
        usable = status == 'approved' and report['id'] not in empty_reports
        daily.append({'date': day.isoformat(), 'status': status, 'report_id': report['id'] if report else None,
                      **finished(per_day[day], has_report=usable)})
        if status != 'approved':
            missing.append(day.isoformat())
    if missing:
        warnings.append(f'{len(missing)} date(s) have no approved report. These dates are gaps, not zero sales.')
    if total['missing_price_rows']:
        warnings.append(f'{total["missing_price_units"]} sold unit(s) have no saved price. Revenue is a known-price subtotal, not a complete total.')
    if total['assumed_price_revenue_vnd']:
        warnings.append('Some revenue uses provisional prices saved at approval.')
    if total['unverified_price_revenue_vnd']:
        warnings.append('Some saved prices have no assumption/confirmation provenance.')
    if total['invalid_quantity_rows'] or empty_reports:
        warnings.append('Some older approved records have missing quantities or no sales rows. Totals are incomplete.')
    if total['uncategorized']:
        warnings.append('Some sold units have no product classification and are shown separately.')
    if legacy_classification_rows:
        warnings.append('Older approvals without a saved category use the current catalog for drinks/cones/toppings classification. Saved quantities and prices are unchanged.')
    result = finished(total, has_report=bool(approved) and bool(total['valid_rows'] or total['invalid_quantity_rows']))
    result['revenue_is_partial'] |= bool(empty_reports)
    result.update(approved_reports=len(approved), draft_reports=sum(r['status'] == 'draft' for r in reports),
                  days_in_period=len(daily), days_without_approved_report=len(missing),
                  legacy_classification_rows=legacy_classification_rows)
    products = [finished(row) for row in ranked.values()]
    products.sort(key=lambda row: (-(row['quantity'] or 0), row['name'], row['product_id'] or ''))
    for row in products:
        row['latest_sale_date'] = row['latest_sale_date'].isoformat()
    return {'summary': result, 'daily': daily, 'products': products, 'missing_dates': missing, 'warnings': warnings}


def activity(conn, t, date_from, date_to, zone, reports):
    """Aggregate each stock item in its own unit; never sum g, ml and pieces."""
    start = datetime.combine(date_from, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    m, sale, report = t['inventory_movements'], t['sales_items'], t['sales_reports']
    usage = dict(conn.execute(select(m.c.item_id, (-func.sum(m.c.signed_quantity)).label('quantity'))
                 .select_from(m.join(sale, m.c.sale_item_id == sale.c.id).join(report, sale.c.report_id == report.c.id))
                 .where(m.c.kind == 'sale', report.c.status == 'approved', report.c.report_date >= date_from,
                        report.c.report_date <= date_to).group_by(m.c.item_id)).all())
    p, line = t['purchases'], t['purchase_items']
    purchase_day = func.coalesce(p.c.purchased_on, func.timezone(zone.key, p.c.purchased_at).cast(Date))
    purchases = {row['item_id']: row for row in conn.execute(select(line.c.item_id,
                    func.sum(line.c.quantity).label('quantity'), func.sum(line.c.total_cost_vnd).label('known_cost'),
                    func.count().label('line_count'), func.sum(case((line.c.total_cost_vnd.is_(None), 1), else_=0)).label('missing_cost_lines'))
                    .select_from(line.join(p, line.c.purchase_id == p.c.id)).where(purchase_day >= date_from, purchase_day <= date_to)
                    .group_by(line.c.item_id)).mappings()}
    adjustments = {row['item_id']: row for row in conn.execute(select(m.c.item_id,
                    func.sum(case((m.c.kind == 'waste', -m.c.signed_quantity), else_=0)).label('waste'),
                    func.sum(case((m.c.kind == 'reversal', m.c.signed_quantity), else_=0)).label('reversal'),
                    func.sum(case(((m.c.kind != 'reversal') & (m.c.signed_quantity > 0), m.c.signed_quantity), else_=0)).label('added'),
                    func.sum(case(((m.c.kind != 'waste') & (m.c.signed_quantity < 0), -m.c.signed_quantity), else_=0)).label('removed'))
                    .where(m.c.kind.in_(['waste', 'correction', 'opening_adjustment', 'reversal']), m.c.occurred_at >= start,
                           m.c.occurred_at < end).group_by(m.c.item_id)).mappings()}
    powder_usage = {}
    if 'ice_cream_daily_usage' in t:
        daily = t['ice_cream_daily_usage']
        powder_usage = dict(conn.execute(select(daily.c.item_id, func.sum(daily.c.quantity))
            .where(daily.c.used_on >= date_from, daily.c.used_on <= date_to).group_by(daily.c.item_id)).all())
    total_usage = {key: usage.get(key, Decimal(0)) + powder_usage.get(key, Decimal(0)) for key in set(usage) | set(powder_usage)}
    stocks = {row['id']: row for row in conn.execute(select(t['stock_items'])).mappings()}
    frozen_names = {}
    for saved in sorted(reports, key=lambda row: row['report_date']):
        if saved['status'] == 'approved':
            for row in (saved.get('approval_snapshot') or {}).get('consumption', []):
                frozen_names[row['item_id']] = row['name']
    ingredient_usage = [{'item_id': key, 'name': frozen_names.get(key, stocks[key]['name']), 'unit': stocks[key]['unit'],
                         'quantity': formatted(value), 'sales_quantity': formatted(usage.get(key, 0)),
                         'ice_cream_quantity': formatted(powder_usage.get(key, 0))}
                        for key, value in total_usage.items() if value]
    ingredient_usage.sort(key=lambda row: (row['unit'], -Decimal(row['quantity']), row['name']))
    entries = []
    for key in set(purchases) | set(adjustments):
        purchased, adjusted, stock = purchases.get(key, {}), adjustments.get(key, {}), stocks[key]
        entries.append({'item_id': key, 'name': stock['name'], 'unit': stock['unit'],
                        'purchased_quantity': formatted(purchased.get('quantity', 0)),
                        'waste_quantity': formatted(adjusted.get('waste', 0)),
                        'reversal_quantity': formatted(adjusted.get('reversal', 0)),
                        'correction_added': formatted(adjusted.get('added', 0)),
                        'correction_removed': formatted(adjusted.get('removed', 0)),
                        'known_purchase_cost_vnd': int(purchased['known_cost']) if purchased.get('known_cost') is not None else None,
                        'missing_cost_lines': purchased.get('missing_cost_lines', 0)})
    entries.sort(key=lambda row: row['name'])
    counts = t['stock_counts']
    count_events = conn.scalar(select(func.count()).select_from(counts).where(counts.c.counted_at >= start, counts.c.counted_at < end))
    return {'ingredient_usage': ingredient_usage, 'inventory_activity': entries, 'physical_counts': count_events}


@router.get('/dashboard')
def dashboard(date_from: date | None = None, date_to: date | None = None):
    zone_name = environment().get('SHOP_TIMEZONE') or 'Asia/Singapore'
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(503, 'SHOP_TIMEZONE is invalid. Use a timezone such as Asia/Singapore.') from None
    with connection(read_only=True) as (conn, t):
        table = t['sales_reports']
        if 'approval_snapshot' not in table.c:
            raise HTTPException(503, 'Run migrations and restart the backend before opening Analytics.')
        latest = conn.scalar(select(func.max(table.c.report_date)).where(table.c.status == 'approved'))
        today = datetime.now(zone).date()
        end = date_to or latest or today
        start = date_from or date.fromordinal(max(1, end.toordinal() - 29))
        if start > end or (end - start).days >= 366 or end == date.max:
            raise HTTPException(400, 'Choose an ordered date range of at most 366 days, ending before 9999-12-31.')
        reports = conn.execute(select(table).where(table.c.report_date >= start, table.c.report_date <= end)
                               .order_by(table.c.report_date)).mappings().all()
        line, product, price = t['sales_items'], t['products'], t['price_versions']
        rows = conn.execute(select(line.c.id, line.c.report_id, line.c.product_id, line.c.raw_item_name,
                                  line.c.quantity, line.c.unit_price_vnd, product.c.name.label('current_name'),
                                  product.c.category.label('current_category'), price.c.assumed.label('price_assumed'))
                            .select_from(line.join(table, line.c.report_id == table.c.id)
                                         .outerjoin(product, line.c.product_id == product.c.id)
                                         .outerjoin(price, line.c.price_version_id == price.c.id))
                            .where(table.c.status == 'approved', table.c.report_date >= start, table.c.report_date <= end)
                            .order_by(table.c.report_date, line.c.id)).mappings().all()
        result = summarize_sales(reports, rows, start, end)
        result.update(activity(conn, t, start, end, zone, reports))
        current = stock_states(conn, t)
        result['current_inventory'] = {'as_of': datetime.now(timezone.utc).isoformat(), 'items': current,
                                       'low_stock_items': sum(r['low_stock'] for r in current),
                                       'unknown_stock_items': sum(not r['balance_known'] for r in current),
                                       'negative_ledger_items': sum(r['negative_ledger'] for r in current)}
        result['range'] = {'date_from': start.isoformat(), 'date_to': end.isoformat(), 'timezone': zone.key,
                           'today': today.isoformat(), 'latest_approved_date': latest.isoformat() if latest else None,
                           'default_basis': '30 days ending on the latest approved report, or today if none exists.'}
        return result
