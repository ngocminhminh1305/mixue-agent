"""Decimal consumption calculations and reproducible approval previews."""
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP, localcontext

from sqlalchemy import func, select

SCALE = Decimal('0.000001')
LIMIT = Decimal('99999999999999.999999')


def number(value):
    return Decimal(str(value))


def amount(value):
    return format(value, 'f')


def unique(values):
    return list(dict.fromkeys(values))


def newest(rows, key):
    result = {}
    for row in rows:
        previous = result.get(row[key])
        if previous is None or previous['version'] < row['version']:
            result[row[key]] = row
    return result


def grouped(rows, key):
    result = defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return result


def calculate_consumption(rows, products, stocks, recipes, components, preparations, inputs, prices, balances):
    """Return a JSON-safe plan. Round once per sale row/raw item, then sum it.

    Prepared bases are expanded to raw inputs only. No prepared-base movements
    are emitted, and no water/ice usage is invented beyond configured recipes.
    """
    blockers, warnings, assumptions, plans = [], [], [], []
    totals = defaultdict(Decimal)
    if not rows:
        blockers.append('The report has no sales rows.')
    duplicates = Counter(row['product_id'] for row in rows if row['product_id'])
    for row in rows:
        label = row['raw_item_name'] or 'Unnamed row'
        errors, used_preparations = [], set()
        product = products.get(row['product_id'])
        recipe = recipes.get(row['product_id'])
        if not product or product.get('is_deleted') or not product.get('active', True):
            blockers.append(f'{label}: choose an available catalog product.')
            continue
        if duplicates[row['product_id']] > 1:
            errors.append('duplicate product rows must be resolved')
        if not label.strip() or not row['raw_item_name'].strip():
            errors.append('receipt name is missing')
        quantity = row['quantity']
        if quantity is None or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            errors.append('quantity must be a non-negative whole number')
        parts = components.get(recipe['id'], []) if recipe else []
        if not parts:
            errors.append('recipe is missing or empty')
        if errors:
            blockers.extend(f'{label}: {error}.' for error in errors)
            continue
        if recipe.get('assumed') or product.get('assumed'):
            assumptions.append(f'Recipe: {product["name"]}')
        usage = defaultdict(Decimal)
        with localcontext() as context:
            context.prec = 60
            for part in parts:
                stock = stocks.get(part['item_id'])
                if not stock or stock.get('is_deleted'):
                    errors.append('recipe references a missing/archived ingredient')
                    continue
                if stock.get('assumed'):
                    assumptions.append(f'Ingredient: {stock["name"]}')
                required = number(part['quantity']) * quantity
                if stock['kind'] != 'prepared':
                    usage[stock['id']] += required
                    continue
                prep = preparations.get(stock['id'])
                raw_parts = inputs.get(prep['id'], []) if prep else []
                if not prep or prep.get('output_quantity') is None or number(prep['output_quantity']) <= 0 or not raw_parts:
                    errors.append(f'preparation inputs/yield missing for {stock["name"]}')
                    continue
                used_preparations.add(prep['id'])
                if prep.get('assumed'):
                    assumptions.append(f'Preparation yield: {stock["name"]}')
                for raw_part in raw_parts:
                    raw = stocks.get(raw_part['item_id'])
                    if not raw or raw.get('is_deleted') or raw['kind'] != 'raw':
                        errors.append(f'{stock["name"]} must be prepared from available raw ingredients')
                        continue
                    if raw.get('assumed'):
                        assumptions.append(f'Ingredient: {raw["name"]}')
                    usage[raw['id']] += required / number(prep['output_quantity']) * number(raw_part['quantity'])
            rounded = {}
            for item_id, value in usage.items():
                stock = stocks[item_id]
                if value < 0 or value > LIMIT:
                    errors.append(f'consumption for {stock["name"]} exceeds supported limits')
                    continue
                if stock['unit'] == 'piece' and value != value.to_integral_value():
                    errors.append(f'{stock["name"]} consumption must be whole pieces')
                    continue
                quantized = value.quantize(SCALE, rounding=ROUND_HALF_UP)
                if value > 0 and quantized == 0:
                    errors.append(f'{stock["name"]} consumption is below supported precision')
                elif quantized:
                    rounded[item_id] = quantized
        if errors:
            blockers.extend(f'{label}: {error}.' for error in unique(errors))
            continue
        price = prices.get(product['id'])
        if not price or price['price_vnd'] is None:
            warnings.append(f'{product["name"]}: selling price is unknown; inventory can still be calculated.')
        elif price.get('assumed'):
            assumptions.append(f'Selling price: {product["name"]}')
        plans.append({'sale_item_id': row['id'], 'product_id': product['id'], 'product_name': product['name'],
                      'product_category': product.get('category'), 'product_size': product.get('size'),
                      'quantity': quantity, 'recipe_id': recipe['id'], 'recipe_version': recipe['version'],
                      'price_version_id': price['id'] if price else None,
                      'unit_price_vnd': price['price_vnd'] if price else None,
                      'unit_price_assumed': price.get('assumed') if price else None,
                      'preparation_version_ids': sorted(used_preparations),
                      'deductions': [{'item_id': key, 'quantity': amount(value)} for key, value in sorted(rounded.items())]})
        for item_id, value in rounded.items():
            totals[item_id] += value
    consumption = []
    for item_id, quantity in sorted(totals.items(), key=lambda pair: pair[0]):
        stock = stocks[item_id]
        before = number(balances.get(item_id, 0))
        after = before - quantity
        known = stock.get('balance_known', stock.get('opening_stock') is not None)
        if not known:
            warnings.append(f'{stock["name"]}: starting stock is unknown. Remaining stock is an estimate until an opening balance or physical count is entered.')
        if after < 0:
            warnings.append(f'{stock["name"]}: ledger balance after deduction is negative ({amount(after)} {stock["unit"]}).')
        if quantity > LIMIT or abs(after) > LIMIT:
            blockers.append(f'{stock["name"]}: total consumption/balance exceeds supported limits.')
        consumption.append({'item_id': item_id, 'name': stock['name'], 'unit': stock['unit'], 'quantity': amount(quantity),
                            'starting_stock_known': known, 'balance_before': amount(before) if known else None,
                            'balance_after': amount(after) if known else None,
                            'ledger_balance_before': amount(before), 'ledger_balance_after': amount(after)})
    return {'rows': plans, 'consumption': consumption, 'blockers': unique(blockers),
            'warnings': unique(warnings), 'assumptions': unique(assumptions)}


def build_preview(conn, tables, report, shop_revision):
    from sales import row_warnings

    def records(name):
        table = tables[name]
        return list(conn.execute(select(table).order_by(*table.primary_key.columns)).mappings())

    rows = list(conn.execute(select(tables['sales_items']).where(tables['sales_items'].c.report_id == report['id'])
                             .order_by(tables['sales_items'].c.id)).mappings())
    products = {row['id']: row for row in records('products')}
    stocks = {row['id']: dict(row) for row in records('stock_items')}
    if 'stock_counts' in tables:
        counted = set(conn.scalars(select(tables['stock_counts'].c.item_id).distinct()))
        for item in stocks.values():
            item['balance_known'] = item['opening_stock'] is not None or item['id'] in counted
    recipes = newest(records('recipe_versions'), 'product_id')
    prices = newest(records('price_versions'), 'product_id')
    prep_versions = newest(records('preparation_versions'), 'preparation_id')
    preparations = {}
    for row in records('preparations'):
        if not row['is_deleted'] and row['id'] in prep_versions:
            version = dict(prep_versions[row['id']])
            version['assumed'] = version['assumed'] or row['assumed']
            preparations[row['output_item_id']] = version
    movement = tables['inventory_movements']
    balances = dict(conn.execute(select(movement.c.item_id, func.sum(movement.c.signed_quantity)).group_by(movement.c.item_id)).all())
    plan = calculate_consumption(rows, products, stocks, recipes,
                                grouped(records('recipe_components'), 'recipe_id'), preparations,
                                grouped(records('preparation_inputs'), 'preparation_version_id'), prices, balances)
    available = {key for key, value in products.items() if value['active'] and not value['is_deleted']}
    issues = row_warnings(rows, available)
    plan['blockers'] = unique(plan['blockers'] + [f'{r["raw_item_name"] or "Unnamed row"}: {issue}'
                                                for r in rows for issue in issues[r['id']]])
    source_warnings = list(report['issues'])
    for row in rows:
        source_warnings.extend(f'{row["raw_item_name"]}: {issue}' for issue in row['issues'])
    for image in conn.execute(select(tables['sales_images']).where(tables['sales_images'].c.report_id == report['id'])
                              .order_by(tables['sales_images'].c.id)).mappings():
        source_warnings.extend((image['extraction'] or {}).get('issues', []))
    plan['warnings'] = unique(plan['warnings'] + source_warnings)
    plan.update(report_id=report['id'], date=report['report_date'].isoformat(), revision=report['revision'],
                shop_revision=shop_revision, recipe_policy='Current catalog at approval', approved=False,
                can_approve=not plan['blockers'])
    canonical = json.dumps(plan, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    plan['preview_token'] = hashlib.sha256(canonical.encode()).hexdigest()
    return plan
