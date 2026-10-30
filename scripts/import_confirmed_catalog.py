"""Compile private confirmed recipes and demo stock, then atomically import them.

Defaults to a dry run. --apply requires a migrated database and a prior backup.
No API or model calls are made. Original receipt and approval history is retained.
"""
import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import insert, select, update

from bobaops.catalog.repository import read_config, write_config
from bobaops.core.database import ROOT, get_engine, get_tables
from bobaops.inventory.routes import fingerprint, stock_states
from bobaops.sales.routes import match_product, matching_index, normalized_name
from bobaops.catalog.schemas import Component, Preparation, Product, ShopConfig, StockItem, check_config


def identity(kind, name):
    return kind + '_' + hashlib.sha256(normalized_name(name).encode()).hexdigest()[:20]


def load_sources(directory, demo_path):
    directory = Path(directory)
    def read(name):
        return json.loads((directory / name).read_text())
    recipes = read('recipes_for_selling_products.json')['recipes']
    toppings = read('extra_toppings_active.json')['toppings']
    index = read('preparations_received.json')['preparations']
    preps = [read(p['source']) for p in index]
    demo = json.loads(Path(demo_path).read_text())
    if demo.get('synthetic') is not True:
        raise ValueError('This importer requires explicitly marked synthetic inventory.')
    return {'recipes': recipes, 'toppings': toppings, 'preparations': preps, 'demo': demo}


def compile_catalog(sources, current):
    """No I/O: construct a reviewable import plan with stable, compatible IDs."""
    stock_aliases = {'Cam cắt lát': 'Cam', 'Chanh cắt lát': 'Chanh', 'Hồng trà': 'Hồng trà lạnh',
                     'Trân châu': 'Trân châu nguyên vị', 'Kem': 'Kem nguyên vị', 'Kem tươi': 'Kem nguyên vị'}
    legacy_stock_names = {'Kem nguyên vị': ['Kem'], 'Bột kem nguyên vị': ['Bột kem'],
                          'Cốc M 500 ml': ['Cốc M (500 ml)'], 'Cốc L 700 ml': ['Cốc L (700 ml)']}
    def canonical(name):
        return stock_aliases.get(name, name)
    oldstocks = {normalized_name(x.name): x for x in current.stock_items}
    stocks = {}
    def stock(name, unit, kind):
        name = canonical(name)
        if name in stocks:
            found = stocks[name]
            if (found.unit, found.kind) != (unit, kind):
                raise ValueError('Conflicting stock identity: ' + name)
            return found
        old = next((oldstocks.get(normalized_name(n)) for n in [name] + legacy_stock_names.get(name, [])
                    if oldstocks.get(normalized_name(n)) and
                    (oldstocks[normalized_name(n)].unit, oldstocks[normalized_name(n)].kind) == (unit, kind)), None)
        result = StockItem(id=old.id if old else identity('stock', name + ':' + unit + ':' + kind),
                           name=name, unit=unit, kind=kind, opening_stock=old.opening_stock if old else None,
                           assumed=kind != 'prepared', notes='Demo purchase costs and stock balances.' if kind != 'prepared' else 'Confirmed preparation; user-approved working batch yield.')
        stocks[name] = result
        return result
    demo = sources['demo']['items']
    quantities = {}
    for line in demo:
        unit = 'piece' if line['unit'] == 'cái' else line['unit']
        item = stock(line['name'], unit, 'packaging' if unit == 'piece' else 'raw')
        pack = Decimal(str(line['carton_quantity']))
        price = line['carton_price_vnd']
        if pack <= 0 or not isinstance(price, int) or price < 0:
            raise ValueError('Invalid demo purchase data.')
        item.purchase_unit = 'thùng'
        item.purchase_quantity = float(pack)
        item.purchase_price_vnd = price
        item.purchase_description = line['carton_description']
        item.cost_per_unit = float((Decimal(price) / pack).quantize(Decimal('0.000001')))
        item.ice_cream_powder = item.name in {'Bột kem nguyên vị', 'Bột matcha'}
        target, threshold = Decimal(str(line['opening_stock'])), Decimal(str(line['low_stock_threshold']))
        if target < 0 or threshold < 0 or (unit == 'piece' and any(x != x.to_integral_value() for x in (pack, target, threshold))):
            raise ValueError('Invalid demo balance or threshold.')
        quantities[item.id] = {'quantity': target, 'threshold': threshold}
    preps = []
    oldpreps = {x.output_item_id: x for x in current.preparations}
    for source in sources['preparations']:
        name, unit = source['output_ingredient'], source['output_unit']
        output = stock(name, unit, 'prepared')
        batch = source['standard_batch']
        parts = []
        for line in batch['tracked_inputs']:
            raw = stock(line['name'], line['unit'], 'raw')
            parts.append(Component(item_id=raw.id, quantity=line['quantity']))
        old = oldpreps.get(output.id)
        manual = name in {'Kem nguyên vị', 'Kem matcha'}
        preps.append(Preparation(id=old.id if old else identity('prep', name), name='Pha ' + name,
                                output_item_id=output.id, output_quantity=batch['usable_output_quantity_' + unit],
                                inputs=parts, assumed=False, deduction_mode='manual_ice_cream' if manual else 'automatic',
                                notes='Actual daily powders are entered in Inventory; batch is reference only.' if manual else 'User-approved input-sum working yield; original boba uses the confirmed 1840 g drained yield. Water and ice are untracked.'))
    # Mixed servings have no confirmed flavour split; actual powders are entered manually.
    mixed = stock('Kem Mix', 'g', 'prepared')
    preps.append(Preparation(id=identity('prep','Kem Mix'), name='Kem Mix — ghi bột thực tế',
                             output_item_id=mixed.id, deduction_mode='manual_ice_cream', assumed=False,
                             notes='No invented flavour ratio or serving weight. Powder consumption is manual.'))
    prepoutputs = {x.output_item_id for x in preps}
    recipes = sources['recipes']
    oldproducts = [x.model_dump() for x in current.products]
    index = matching_index(oldproducts, [])
    products, aliases = [], []
    used_ids = set()
    prepared_names = {canonical(p['output_ingredient']) for p in sources['preparations']} | {'Kem Mix'}
    jelly_boba = {'Thạch dừa','Thạch đào','Thạch đường đen','Trân châu trắng','Trân châu nguyên vị','Lô hội'}
    for recipe in recipes:
        name = recipe['selling_name']
        key = normalized_name(name)
        iscone = key.startswith('kem ốc quế')
        sundae = 'sundae' in key
        coffee = key.startswith(('cafe ', 'americano'))
        category = 'cone' if iscone else 'sundae' if sundae else 'coffee' if coffee else 'milk_tea' if key.startswith(('trà sữa','sữa')) else 'fruit_tea'
        size = None if iscone or sundae else 'M' if coffee else recipe['size']
        identifier = match_product(name, index)
        # Preserve these explicitly reconciled older product identities.
        legacy = {'Trà Xanh Chanh':'Trà chanh xanh (size L)', 'Kem Ốc Quế Nguyên Vị':'Kem ốc quế',
                  'Trà Cam Dâu Tây':'Trà chanh dâu tây (size L)'}
        if identifier is None and name in legacy:
            identifier = match_product(legacy[name], index)
        identifier = identifier or identity('product', name)
        if identifier in used_ids:
            raise ValueError('Two products resolved to the same identity: ' + name)
        used_ids.add(identifier)
        components = defaultdict(lambda: Decimal(0))
        for line in recipe['components']:
            ingredient = canonical(line['name'])
            if iscone and ingredient in {'Kem nguyên vị','Kem matcha'} and len([c for c in recipe['components'] if c['name'].startswith('Kem')]) > 1:
                ingredient = 'Kem Mix'
            item = stock(ingredient, line['unit'], 'prepared' if ingredient in prepared_names else 'packaging' if line['unit'] == 'piece' else 'raw')
            value = line['quantity']
            if value is None:
                if item.id not in prepoutputs:
                    raise ValueError('Unknown quantity for non-manual ingredient: ' + ingredient)
                components[item.id] = None
            elif components[item.id] is not None:
                components[item.id] += Decimal(str(value))
        if not iscone:
            cup = 'Cốc U 400 ml' if recipe.get('cup_type') == 'U' or recipe.get('cup_ml') == 400 else 'Cốc M 500 ml' if recipe['cup_ml'] == 500 else 'Cốc L 700 ml'
            components[stocks[cup].id] += 1
            if not sundae:
                straw = 'Ống hút lớn' if any(canonical(c['name']) in jelly_boba for c in recipe['components']) else 'Ống hút nhỏ'
                components[stocks[straw].id] += 1
        product = Product(id=identifier, name=name, category=category, size=size, price_vnd=recipe['selling_price_vnd'],
                          active=True, assumed=False, components=[Component(item_id=k,quantity=None if v is None else float(v)) for k,v in components.items()],
                          notes='Confirmed selling name and recipe. Cup type is a packaging component. Ice-cream powders recorded manually; null ice-cream weights intentionally unknown.')
        products.append(product)
        old = next((p for p in oldproducts if p['id'] == identifier), None)
        if old and normalized_name(old['name']) != key:
            aliases.append({'name':old['name'],'product_id':identifier})
    for topping in sources['toppings']:
        name = 'Thêm ' + topping['name']
        item = stocks[canonical(topping['name'])]
        identifier = match_product(name,index) or identity('product',name)
        products.append(Product(id=identifier,name=name,category='topping',size=None,price_vnd=topping['price_vnd'],active=True,assumed=False,
                                components=[Component(item_id=item.id,quantity=topping['portion_quantity'])],notes='Confirmed extra topping; no extra cup or straw.'))
    config = ShopConfig(revision=current.revision,stock_items=list(stocks.values()),products=products,preparations=preps,questions=[])
    check_config(config)
    required = {x.id for x in config.stock_items if x.kind != 'prepared'}
    if set(quantities) != required:
        raise ValueError('Demo stock does not cover the complete purchased-ingredient catalog.')
    return {'config':config,'inventory':quantities,'aliases':aliases,'import_id':fingerprint(sources),
            'summary':{'main_products':len(recipes),'extra_toppings':len(sources['toppings']),'purchased_items':len(quantities),'preparations':len(preps),'synthetic_inventory':True}}


def apply_catalog(plan):
    """One transaction for versions, aliases, demo counts, thresholds and import marker."""
    t = get_tables()
    config = plan['config']
    with get_engine().begin() as conn:
        conn.execute(select(t['shop_state'].c.revision).where(t['shop_state'].c.id==1).with_for_update()).one()
        previous = conn.execute(select(t['catalog_imports']).where(t['catalog_imports'].c.id==plan['import_id'])).mappings().first()
        if previous:
            return {**previous['summary'],'revision':previous['config_revision'],'replayed':True}
        saved = write_config(config,connection=conn)
        ids = sorted(plan['inventory'])
        conn.execute(select(t['stock_items'].c.id).where(t['stock_items'].c.id.in_(ids)).order_by(t['stock_items'].c.id).with_for_update()).all()
        before = {r['id']:r for r in stock_states(conn,t,ids)}
        now = datetime.now(timezone.utc)
        for item_id, values in plan['inventory'].items():
            opid, movement = uuid4().hex, uuid4().hex
            balance = Decimal(before[item_id]['ledger_estimate'])
            target = values['quantity']
            delta = target-balance
            payload={'item_id':item_id,'actual_quantity':str(target),'reason':'Synthetic demo stock baseline from confirmed catalog import.','catalog_import':plan['import_id']}
            conn.execute(insert(t['inventory_operations']).values(id=opid,kind='count',request_hash=fingerprint(payload),payload=payload,result={'synthetic':True}))
            if delta:
                conn.execute(insert(t['inventory_movements']).values(id=movement,item_id=item_id,kind='correction',signed_quantity=delta,occurred_at=now,note=payload['reason']))
            conn.execute(insert(t['stock_counts']).values(id=opid,item_id=item_id,counted_quantity=target,ledger_balance_before=balance,adjustment_quantity=delta,movement_id=movement if delta else None,counted_at=now))
            conn.execute(update(t['stock_items']).where(t['stock_items'].c.id==item_id).values(low_stock_threshold=values['threshold']))
        for alias in plan['aliases']:
            found = conn.execute(select(t['product_aliases']).where(t['product_aliases'].c.name==alias['name'])).mappings().first()
            if not found:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                conn.execute(pg_insert(t['product_aliases']).values(**alias).on_conflict_do_nothing())
        conn.execute(insert(t['catalog_imports']).values(id=plan['import_id'],config_revision=saved.revision,summary=plan['summary'],synthetic_inventory=True))
        return {**plan['summary'],'revision':saved.revision,'replayed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,default=ROOT/'data/private/confirmed_catalog')
    parser.add_argument('--demo',type=Path,default=ROOT/'data/private/demo/inventory_2026-10-30.json')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    plan=compile_catalog(load_sources(args.sources,args.demo),read_config())
    review=ROOT/'data/private/catalog_import_plan.json'
    review.write_text(json.dumps({'config':plan['config'].model_dump(mode='json'),'summary':plan['summary'],'aliases':plan['aliases'],'inventory':plan['inventory']},ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps(apply_catalog(plan) if args.apply else {**plan['summary'],'dry_run':True,'review_file':str(review)},ensure_ascii=False))


if __name__=='__main__':
    main()
