"""Opt-in integration checks: isolated schemas in a separate *_test database."""
import os
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import MetaData, create_engine, text
from fastapi.testclient import TestClient
from PIL import Image

import app

from database import database_url
from catalog_repository import StaleRevision, read_config, write_config
from shop_config import ShopConfig, StockItem
from test_shop_config import sample


@unittest.skipUnless(os.environ.get('BOBAOPS_TEST_DATABASE'), 'Set BOBAOPS_TEST_DATABASE to opt in.')
class PostgresTests(unittest.TestCase):
    def setUp(self):
        target = os.environ['BOBAOPS_TEST_DATABASE']
        url = database_url()
        if not target.endswith('_test') or target == url.database:
            raise ValueError('Tests require a separate database whose name ends with _test.')
        self.schema = 'test_' + uuid4().hex
        self.admin = create_engine(url.set(database=target))
        self.addCleanup(self.admin.dispose)
        with self.admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA {self.schema}'))
        self.addCleanup(self.drop_schema)
        self.engine = create_engine(url.set(database=target), connect_args={'options': f'-csearch_path={self.schema}'})
        self.addCleanup(self.engine.dispose)
        directory = Path(__file__).resolve().parents[1] / 'migrations/versions'
        with self.engine.begin() as conn:
            for source in sorted(directory.glob('*.sql')):
                for statement in source.read_text().split(';'):
                    if statement.strip():
                        conn.exec_driver_sql(statement)
        metadata = MetaData()
        metadata.reflect(bind=self.engine)
        for module in ('catalog_repository', 'sales', 'inventory_api'):
            for name, value in [('get_engine', self.engine), ('get_tables', metadata.tables)]:
                mocked = patch(module + '.' + name, return_value=value)
                mocked.start()
                self.addCleanup(mocked.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        mocked = patch('sales.IMAGE_DIR', Path(temp.name))
        mocked.start()
        self.addCleanup(mocked.stop)
        for name in ('sales.storage_backend', 'inventory_api.storage_backend'):
            mocked = patch(name, return_value='postgres')
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = TestClient(app.app)
        self.config = ShopConfig.model_validate(sample())

    def drop_schema(self):
        with self.admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {self.schema} CASCADE'))

    def scalar(self, sql):
        with self.engine.connect() as conn:
            return conn.scalar(text(sql))

    def test_import_roundtrip_and_repeat_import_refused(self):
        saved = write_config(self.config, initial_import=True)
        loaded = read_config()
        self.assertEqual(loaded.revision, saved.revision)
        self.assertEqual(loaded.questions, saved.questions)
        for field in ('stock_items', 'products', 'preparations'):
            self.assertEqual({x.id: x.model_dump() for x in getattr(loaded, field)},
                             {x.id: x.model_dump() for x in getattr(saved, field)})
        with self.assertRaises(ValueError):
            write_config(self.config, initial_import=True)
        self.assertEqual(self.scalar('SELECT revision FROM shop_state'), 1)

    def test_recipe_and_price_history_only_change_when_needed(self):
        saved = write_config(self.config, initial_import=True)
        saved = write_config(saved)
        self.assertEqual(self.scalar('SELECT count(*) FROM recipe_versions'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM price_versions'), 1)
        saved.products[0].components[0].quantity = 200
        saved.products[0].price_vnd = 30000
        write_config(saved)
        self.assertEqual(self.scalar('SELECT count(*) FROM recipe_versions'), 2)
        self.assertEqual(self.scalar('SELECT price_vnd FROM price_versions WHERE version = 1'), 25000)
        self.assertEqual(self.scalar('SELECT quantity FROM recipe_components JOIN recipe_versions ON recipe_id = id WHERE version = 1'), 160)

    def test_stale_edit_rolls_back(self):
        write_config(self.config, initial_import=True)
        self.config.products[0].price_vnd = 999
        with self.assertRaises(StaleRevision):
            write_config(self.config)
        self.assertEqual(read_config().products[0].price_vnd, 25000)
        self.assertEqual(self.scalar('SELECT count(*) FROM config_history'), 1)

    def test_opening_balance_adjustments_are_not_double_counted(self):
        self.config.stock_items[0].opening_stock = 1000
        saved = write_config(self.config, initial_import=True)
        saved = write_config(saved)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 1)
        saved.stock_items[0].opening_stock = 1200
        write_config(saved)
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id = 'powder'"), 1200)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 2)

    def test_unit_change_is_rejected_without_partial_save(self):
        saved = write_config(self.config, initial_import=True)
        saved.stock_items[0].unit = 'ml'
        with self.assertRaises(ValueError):
            write_config(saved)
        self.assertEqual(next(x.unit for x in read_config().stock_items if x.id == 'powder'), 'g')
        self.assertEqual(self.scalar('SELECT revision FROM shop_state'), 1)

    def post_photo(self, color='red', day='2026-10-22', quantity=3):
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4), color).save(buffer, format='PNG')
        report = {'date': day, 'items': [{'item_name': 'Tea M (MT)', 'quantity': quantity, 'issues': []}], 'issues': []}
        return self.client.post('/api/sales/drafts', data={'report': json.dumps(report), 'report_date': day},
                                files={'image': ('photo.png', buffer.getvalue(), 'image/png')})

    def test_draft_persists_photo_and_matching_without_inventory_changes(self):
        write_config(self.config, initial_import=True)
        response = self.post_photo()
        self.assertEqual(response.status_code, 201, response.text)
        draft = response.json()
        self.assertEqual(draft['items'][0]['product_id'], 'drink')
        self.assertEqual(draft['items'][0]['quantity'], 3)
        self.assertEqual(self.client.get('/api/sales/reports/' + draft['id']).json(), draft)
        self.assertEqual(self.client.get(draft['images'][0]['url']).status_code, 200)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 1)
        self.assertEqual(self.client.get('/api/sales/reports').json()[0]['image_count'], 1)

    def test_duplicate_upload_and_overlapping_rows(self):
        write_config(self.config, initial_import=True)
        first = self.post_photo().json()
        self.assertEqual(self.post_photo().status_code, 409)
        # The same image cannot be counted under another date either.
        self.assertEqual(self.post_photo(day='2026-10-23').status_code, 409)
        second = self.post_photo(color='blue').json()
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(len(second['images']), 2)
        self.assertEqual([r['quantity'] for r in second['items']], [3, 3])
        self.assertTrue(all(any('overlap' in i for i in row['issues']) for row in second['items']))
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 1)

    def test_corrections_remove_overlap_and_reject_stale_save(self):
        write_config(self.config, initial_import=True)
        first = self.post_photo().json()
        second = self.post_photo(color='blue').json()
        row = first['items'][0]
        payload = {'revision': first['revision'], 'date': first['date'], 'items': [
            {'id': row['id'], 'raw_item_name': row['raw_item_name'], 'product_id': 'drink', 'quantity': 7}]}
        url = '/api/sales/reports/' + first['id']
        self.assertEqual(self.client.put(url, json=payload).status_code, 409)
        payload['revision'] = second['revision']
        response = self.client.put(url, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(len(saved['items']), 1)
        self.assertEqual(saved['items'][0]['quantity'], 7)
        self.assertEqual(saved['items'][0]['issues'], [])
        self.assertEqual(len(saved['images']), 2)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)

    def test_editing_non_draft_and_changing_to_occupied_date_are_rejected(self):
        write_config(self.config, initial_import=True)
        first = self.post_photo().json()
        self.post_photo(color='blue', day='2026-10-23')
        url = '/api/sales/reports/' + first['id']
        edit = {'revision': first['revision'], 'date': '2026-10-23', 'items': []}
        self.assertEqual(self.client.put(url, json=edit).status_code, 409)
        self.assertEqual(len(self.client.get(url).json()['items']), 1)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE sales_reports SET status='approved', approved_at=CURRENT_TIMESTAMP WHERE id=:id"), {'id': first['id']})
        edit['date'] = first['date']
        self.assertEqual(self.client.put(url, json=edit).status_code, 409)
        self.assertEqual(self.post_photo(color='green').status_code, 409)

    def test_failed_save_rolls_back_database_and_removes_new_photo(self):
        import sales
        from sqlalchemy.exc import SQLAlchemyError
        write_config(self.config, initial_import=True)
        with patch('sales.details', side_effect=SQLAlchemyError('simulated failure')):
            self.assertEqual(self.post_photo().status_code, 503)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_images'), 0)
        self.assertEqual(list(sales.IMAGE_DIR.iterdir()), [])

    def test_draft_cannot_take_rows_from_another_report(self):
        write_config(self.config, initial_import=True)
        first = self.post_photo().json()
        other = self.post_photo(color='blue', day='2026-10-23').json()
        body = {'revision': first['revision'], 'date': first['date'], 'items': [
            {'id': other['items'][0]['id'], 'raw_item_name': 'Tea', 'quantity': 7, 'product_id': 'drink'}]}
        self.assertEqual(self.client.put('/api/sales/reports/' + first['id'], json=body).status_code, 400)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_items'), 2)

    def test_delete_draft_removes_only_its_rows_photos_and_allows_reupload(self):
        import sales
        write_config(self.config, initial_import=True)
        first = self.post_photo().json()
        other = self.post_photo(color='blue', day='2026-10-23').json()
        url = '/api/sales/reports/' + first['id']
        result = self.client.delete(url + '?revision=' + str(first['revision']))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(first['images'][0]['url']).status_code, 404)
        self.assertEqual(self.client.get('/api/sales/reports/' + other['id']).status_code, 200)
        self.assertEqual(len(list(sales.IMAGE_DIR.iterdir())), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_items'), 1)
        self.assertEqual(self.post_photo().status_code, 201)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)

    def test_delete_requires_current_revision_and_draft_status(self):
        write_config(self.config, initial_import=True)
        first = self.post_photo().json()
        current = self.post_photo(color='blue').json()
        url = '/api/sales/reports/' + first['id']
        self.assertEqual(self.client.delete(url + '?revision=' + str(first['revision'])).status_code, 409)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE sales_reports SET status='approved', approved_at=CURRENT_TIMESTAMP WHERE id=:id"), {'id': first['id']})
        self.assertEqual(self.client.delete(url + '?revision=' + str(current['revision'])).status_code, 409)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_images'), 2)

    def test_rematch_old_draft_preserves_names_quantities_and_manual_choices(self):
        self.config.products[0].name = 'Nước chanh tươi lạnh (size L)'
        self.config.products[0].size = 'L'
        saved = write_config(self.config, initial_import=True)
        draft = self.post_photo().json()  # "Tea M" is initially unmatched.
        self.assertIsNone(draft['items'][0]['product_id'])
        saved.products[0].name = 'Tea (size M)'
        saved.products[0].size = 'M'
        write_config(saved)
        url = '/api/sales/reports/' + draft['id'] + '/match'
        response = self.client.post(url, json={'revision': draft['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        matched = response.json()
        self.assertEqual(matched['matched_count'], 1)
        self.assertEqual(matched['items'][0]['product_id'], 'drink')
        self.assertEqual(matched['items'][0]['raw_item_name'], 'Tea M (MT)')
        self.assertEqual(matched['items'][0]['quantity'], 3)
        self.assertEqual(self.client.post(url, json={'revision': draft['revision']}).status_code, 409)
        # A later catalog name change must not replace an existing selection.
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE products SET name='Other tea' WHERE id='drink'"))
        response = self.client.post(url, json={'revision': matched['revision']})
        self.assertEqual(response.json()['matched_count'], 0)
        self.assertEqual(response.json()['items'][0]['product_id'], 'drink')
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)

    def consumption_preview(self, draft):
        response = self.client.get('/api/sales/reports/' + draft['id'] + '/consumption')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def approval_body(self, preview, **overrides):
        return {'revision': preview['revision'], 'preview_token': preview['preview_token'],
                'confirm_complete': True, 'accept_assumptions': True, **overrides}

    def test_preview_and_approval_freeze_versions_and_deduct_raw_stock_once(self):
        from decimal import Decimal
        self.config.stock_items[0].opening_stock = 1000
        saved = write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        self.assertTrue(preview['can_approve'])
        self.assertEqual(Decimal(preview['consumption'][0]['quantity']), 120)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'"), 0)
        url = '/api/sales/reports/' + draft['id']
        body = self.approval_body(preview)
        response = self.client.post(url + '/approve', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        approved = response.json()
        self.assertEqual(approved['report']['status'], 'approved')
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 880)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'"), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_preparation_versions'), 1)
        self.assertEqual(self.client.post(url + '/approve', json=body).status_code, 409)
        self.assertEqual(self.client.delete(url + '?revision=' + str(approved['report']['revision'])).status_code, 409)
        saved.preparations[0].output_quantity = 1000
        saved.products[0].name = 'Renamed tea'
        saved.products[0].price_vnd = 30000
        write_config(saved)
        frozen = self.consumption_preview(approved['report'])
        self.assertEqual(frozen, approved['preview'])
        self.assertEqual(frozen['rows'][0]['unit_price_vnd'], 25000)
        self.assertEqual(self.client.get(url).json()['products'][0]['name'], 'Tea M')

    def test_approval_requires_complete_review_and_assumption_acceptance(self):
        write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        url = '/api/sales/reports/' + draft['id'] + '/approve'
        for override in ({'confirm_complete': False}, {'accept_assumptions': False}):
            self.assertEqual(self.client.post(url, json=self.approval_body(preview, **override)).status_code, 400)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)
        self.assertEqual(self.scalar("SELECT status FROM sales_reports"), 'draft')

    def test_unmatched_and_overlapping_sales_cannot_be_approved(self):
        write_config(self.config, initial_import=True)
        self.post_photo()
        draft = self.post_photo(color='blue').json()
        preview = self.consumption_preview(draft)
        self.assertFalse(preview['can_approve'])
        self.assertEqual(self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview)).status_code, 400)
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE sales_items SET product_id=NULL WHERE report_id=:id'), {'id': draft['id']})
        self.assertFalse(self.consumption_preview(draft)['can_approve'])
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)

    def test_catalog_or_stock_changes_invalidate_a_reviewed_preview(self):
        saved = write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        url = '/api/sales/reports/' + draft['id'] + '/approve'
        saved.preparations[0].output_quantity = 1000
        write_config(saved)
        self.assertEqual(self.client.post(url, json=self.approval_body(preview)).status_code, 409)
        preview = self.consumption_preview(draft)
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO inventory_movements (id,item_id,kind,signed_quantity) VALUES ('correction','powder','correction',10)"))
        self.assertEqual(self.client.post(url, json=self.approval_body(preview)).status_code, 409)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'"), 0)

    def test_failed_approval_rolls_back_movements_status_and_version_links(self):
        from sqlalchemy.exc import SQLAlchemyError
        write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        with patch('sales.details', side_effect=SQLAlchemyError('simulated failure after movement writes')):
            result = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_preparation_versions'), 0)
        self.assertIsNone(self.scalar('SELECT recipe_id FROM sales_items'))
        self.assertEqual(self.scalar('SELECT status FROM sales_reports'), 'draft')

    def test_concurrent_approval_cannot_double_deduct(self):
        from concurrent.futures import ThreadPoolExecutor
        write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        url = '/api/sales/reports/' + draft['id'] + '/approve'
        body = self.approval_body(preview)
        def approve(_):
            return TestClient(app.app).post(url, json=body).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(approve, range(2)))
        self.assertEqual(statuses, [200, 409])
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'"), 1)

    def stock_state(self, item='powder'):
        response = self.client.get('/api/inventory')
        self.assertEqual(response.status_code, 200, response.text)
        return next(r for r in response.json()['items'] if r['id'] == item)

    def stock_entry(self, kind='count', item='powder', **values):
        data = {'request_id': str(uuid4()), 'item_id': item, 'balance_token': self.stock_state(item)['balance_token']}
        if kind != 'threshold':
            data['reason'] = 'Physical count' if kind == 'count' else 'Spill'
        return {**data, **values}

    def test_inventory_purchase_is_atomic_and_retry_safe(self):
        from decimal import Decimal
        self.config.stock_items.append(StockItem(id='cups', name='Cup M', kind='packaging', unit='piece', opening_stock=10))
        write_config(self.config, initial_import=True)
        body = {'request_id': str(uuid4()), 'purchased_on': '2026-10-22', 'note': 'Supplier receipt',
                'items': [{'item_id': 'powder', 'quantity': '1000', 'total_cost_vnd': 50000},
                          {'item_id': 'cups', 'quantity': '100', 'total_cost_vnd': None}]}
        response = self.client.post('/api/inventory/purchases', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.client.post('/api/inventory/purchases', json=body).json()['replayed'], True)
        self.assertEqual(self.scalar('SELECT count(*) FROM purchases'), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='purchase'"), 2)
        self.assertEqual(Decimal(self.stock_state('cups')['quantity']), 110)
        # Buying supplies does not invent an unknown opening baseline.
        self.assertIsNone(self.stock_state()['quantity'])
        self.assertEqual(Decimal(self.stock_state()['ledger_estimate']), 1000)
        body['items'][0]['quantity'] = '2000'
        self.assertEqual(self.client.post('/api/inventory/purchases', json=body).status_code, 409)

    def test_count_establishes_known_stock_and_waste_preserves_history(self):
        from decimal import Decimal
        write_config(self.config, initial_import=True)
        body = self.stock_entry(actual_quantity='1100', reason='Weekly count')
        response = self.client.post('/api/inventory/counts', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.client.post('/api/inventory/counts', json=body).json()['replayed'], True)
        self.assertEqual(Decimal(self.stock_state()['quantity']), 1100)
        self.assertTrue(self.scalar("SELECT opening_stock_known FROM inventory_balances WHERE item_id='powder'"))
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        self.assertTrue(preview['consumption'][0]['starting_stock_known'])
        self.assertEqual(Decimal(preview['consumption'][0]['balance_after']), 980)
        waste = self.stock_entry(kind='waste', quantity='25', reason='Spilled powder')
        self.assertEqual(self.client.post('/api/inventory/waste', json=waste).status_code, 201)
        self.assertEqual(Decimal(self.stock_state()['quantity']), 1075)
        history = self.client.get('/api/inventory/history?item_id=powder').json()
        self.assertEqual(history['counts'][0]['reason'], 'Weekly count')
        self.assertTrue(any(r['kind'] == 'waste' and r['note'] == 'Spilled powder' for r in history['movements']))

    def test_actual_count_uses_delta_and_rejects_stale_balance(self):
        from decimal import Decimal
        self.config.stock_items[0].opening_stock = 1200
        write_config(self.config, initial_import=True)
        body = self.stock_entry(actual_quantity='1100')
        response = self.client.post('/api/inventory/counts', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(Decimal(response.json()['adjustment']), -100)
        self.assertEqual(Decimal(self.stock_state()['quantity']), 1100)
        stale = {**body, 'request_id': str(uuid4()), 'actual_quantity': '900'}
        self.assertEqual(self.client.post('/api/inventory/counts', json=stale).status_code, 409)
        self.assertEqual(Decimal(self.stock_state()['quantity']), 1100)

    def test_zero_delta_count_is_recorded_without_zero_movement(self):
        write_config(self.config, initial_import=True)
        response = self.client.post('/api/inventory/counts', json=self.stock_entry(actual_quantity='0'))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.stock_state()['quantity'], '0.000000')
        self.assertEqual(self.scalar('SELECT count(*) FROM stock_counts'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)

    def test_low_stock_threshold_and_opening_edit_protection(self):
        saved = write_config(self.config, initial_import=True)
        count = self.client.post('/api/inventory/counts', json=self.stock_entry(actual_quantity='100'))
        self.assertEqual(count.status_code, 201, count.text)
        level = self.stock_entry(kind='threshold', threshold='150')
        self.assertEqual(self.client.post('/api/inventory/thresholds', json=level).status_code, 200)
        self.assertTrue(self.stock_state()['low_stock'])
        saved.stock_items[0].opening_stock = 1000
        with self.assertRaises(ValueError):
            write_config(saved)
        self.assertEqual(self.stock_state()['quantity'], '100.000000')
        disable = self.stock_entry(kind='threshold', threshold=None)
        self.assertEqual(self.client.post('/api/inventory/thresholds', json=disable).status_code, 200)
        self.assertFalse(self.stock_state()['low_stock'])

    def test_invalid_purchase_rolls_back_all_lines_and_prepared_stock_is_rejected(self):
        self.config.stock_items.append(StockItem(id='cups', name='Cup M', kind='packaging', unit='piece'))
        write_config(self.config, initial_import=True)
        data = {'request_id': str(uuid4()), 'purchased_on': '2026-10-22',
                'items': [{'item_id': 'powder', 'quantity': '1000'}, {'item_id': 'cups', 'quantity': '1.5'}]}
        self.assertEqual(self.client.post('/api/inventory/purchases', json=data).status_code, 400)
        self.assertEqual(self.scalar('SELECT count(*) FROM purchases'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_operations'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)
        data['request_id'] = str(uuid4())
        data['items'] = [{'item_id': 'base', 'quantity': '1000'}]
        self.assertEqual(self.client.post('/api/inventory/purchases', json=data).status_code, 400)

    def test_inventory_change_invalidates_sales_consumption_preview(self):
        write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        self.client.post('/api/inventory/counts', json=self.stock_entry(actual_quantity='1000'))
        response = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'"), 0)

    def test_concurrent_retries_add_one_purchase_only(self):
        from concurrent.futures import ThreadPoolExecutor
        write_config(self.config, initial_import=True)
        data = {'request_id': str(uuid4()), 'purchased_on': '2026-10-22', 'items': [{'item_id': 'powder', 'quantity': '1000'}]}
        def purchase(_):
            return TestClient(app.app).post('/api/inventory/purchases', json=data).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(purchase, range(2))), [201, 201])
        self.assertEqual(self.scalar('SELECT count(*) FROM purchases'), 1)
        self.assertEqual(self.stock_state()['ledger_estimate'], '1000.000000')

    def table_fixture(self):
        self.config.stock_items.append(StockItem(id='cups', name='Cup M', kind='packaging', unit='piece'))
        write_config(self.config, initial_import=True)
        return {'request_id': str(uuid4()), 'items': [
            {'item_id': 'powder', 'balance_token': self.stock_state()['balance_token'], 'actual_quantity': '1200', 'reason': 'Weekly count'},
            {'item_id': 'cups', 'balance_token': self.stock_state('cups')['balance_token'], 'actual_quantity': '0', 'reason': 'Weekly count'},
        ]}

    def test_table_count_save_is_atomic_and_retry_safe(self):
        body = self.table_fixture()
        response = self.client.post('/api/inventory/counts/batch', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['entry_count'], 2)
        self.assertEqual(self.stock_state()['quantity'], '1200.000000')
        self.assertEqual(self.stock_state('cups')['quantity'], '0.000000')
        self.assertEqual(self.scalar('SELECT count(*) FROM stock_counts'), 2)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 1)
        self.assertTrue(self.client.post('/api/inventory/counts/batch', json=body).json()['replayed'])
        self.assertEqual(self.scalar('SELECT count(*) FROM stock_counts'), 2)
        history = self.client.get('/api/inventory/history').json()
        self.assertTrue(all(c['reason'] == 'Weekly count' for c in history['counts']))

    def test_stale_row_rolls_back_entire_table_count(self):
        body = self.table_fixture()
        body['items'][1]['balance_token'] = '0' * 64
        response = self.client.post('/api/inventory/counts/batch', json=body)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.scalar('SELECT count(*) FROM stock_counts'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_operations'), 0)

    def test_saving_one_table_row_leaves_other_items_unchanged(self):
        body = self.table_fixture()
        body['items'] = body['items'][:1]
        response = self.client.post('/api/inventory/counts/batch', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.stock_state()['quantity'], '1200.000000')
        self.assertIsNone(self.stock_state('cups')['quantity'])
        self.assertEqual(self.scalar('SELECT count(*) FROM stock_counts'), 1)

    def test_table_waste_and_thresholds_save_multiple_rows(self):
        body = self.table_fixture()
        self.assertEqual(self.client.post('/api/inventory/counts/batch', json=body).status_code, 201)
        waste = {'request_id': str(uuid4()), 'items': [
            {'item_id': 'powder', 'balance_token': self.stock_state()['balance_token'], 'quantity': '100', 'reason': 'Spill'},
            {'item_id': 'cups', 'balance_token': self.stock_state('cups')['balance_token'], 'quantity': '1', 'reason': 'Damage'},
        ]}
        response = self.client.post('/api/inventory/waste/batch', json=waste)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.stock_state()['quantity'], '1100.000000')
        self.assertEqual(self.stock_state('cups')['quantity'], '-1.000000')
        thresholds = {'request_id': str(uuid4()), 'items': [
            {'item_id': 'powder', 'balance_token': self.stock_state()['balance_token'], 'threshold': '1500'},
            {'item_id': 'cups', 'balance_token': self.stock_state('cups')['balance_token'], 'threshold': None},
        ]}
        response = self.client.post('/api/inventory/thresholds/batch', json=thresholds)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.stock_state()['low_stock'])
        self.assertIsNone(self.stock_state('cups')['low_stock_threshold'])

    def test_dashboard_approved_sales_frozen_prices_and_current_stock(self):
        from decimal import Decimal
        self.config.stock_items[0].opening_stock = 1000
        saved = write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        approved = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(approved.status_code, 200, approved.text)
        saved.products[0].price_vnd = 50000
        saved.products[0].name = 'Renamed tea M'
        write_config(saved)
        response = self.client.get('/api/analytics/dashboard?date_from=2026-10-22&date_to=2026-10-23')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['summary']['drinks'], 3)
        self.assertEqual(data['summary']['revenue_vnd'], 75000)
        self.assertEqual(data['products'][0]['name'], 'Tea M')
        self.assertIsNone(data['daily'][1]['revenue_vnd'])
        self.assertEqual(Decimal(data['ingredient_usage'][0]['quantity']), Decimal(120))
        self.assertEqual(Decimal(data['current_inventory']['items'][0]['quantity']), Decimal(880))
        empty = self.client.get('/api/analytics/dashboard?date_from=2026-10-23&date_to=2026-10-23').json()
        self.assertEqual(empty['ingredient_usage'], [])
        self.assertIsNone(empty['summary']['revenue_vnd'])
        self.assertEqual(empty['current_inventory']['items'], data['current_inventory']['items'])

    def test_dashboard_drafts_excluded_and_range_validated(self):
        write_config(self.config, initial_import=True)
        self.post_photo()
        response = self.client.get('/api/analytics/dashboard?date_from=2026-10-22&date_to=2026-10-22')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['summary']['draft_reports'], 1)
        self.assertIsNone(response.json()['summary']['drinks'])
        for query in ('date_from=2026-10-23&date_to=2026-10-22',
                      'date_from=2025-01-01&date_to=2026-10-22'):
            self.assertEqual(self.client.get('/api/analytics/dashboard?' + query).status_code, 400)
        self.assertEqual(self.client.get('/api/analytics/dashboard?date_from=bad').status_code, 422)


    def test_assistant_reads_current_stock_and_recipes_without_writes(self):
        import assistant
        self.config.stock_items[0].opening_stock = 1000
        write_config(self.config, initial_import=True)
        before = self.scalar('SELECT count(*) FROM inventory_movements')
        stocks = assistant.lookup('read_stock', assistant.SearchArgs(search='powder'))
        self.assertEqual(stocks['matched_count'], 1)
        self.assertEqual(stocks['rows'][0]['quantity'], '1000.000000')
        self.assertNotIn('balance_token', stocks['rows'][0])
        recipes = assistant.lookup('read_recipes', assistant.SearchArgs(search='tea'))
        row = recipes['rows'][0]
        self.assertEqual(row['price_vnd'], 25000)
        self.assertEqual(row['components_per_serving'][0]['name'], 'Tea base')
        self.assertEqual(float(row['components_per_serving'][0]['quantity']), 160)
        self.assertEqual(float(row['preparations'][0]['output_quantity']), 2000)
        self.assertEqual(float(row['preparations'][0]['inputs'][0]['quantity']), 500)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), before)
        self.assertEqual(self.scalar('SELECT revision FROM shop_state'), 1)

    def test_assistant_chat_uses_real_database_and_mocked_provider(self):
        import httpx
        import json
        self.config.stock_items[0].opening_stock = 1000
        write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        approval = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(approval.status_code, 200, approval.text)
        before = self.scalar('SELECT count(*) FROM inventory_movements')
        call = {'id': 'sales1', 'type': 'function', 'function': {'name': 'read_sales',
                'arguments': json.dumps({'date_from': '2026-10-22', 'date_to': '2026-10-22', 'view': 'summary'})}}
        responses = [httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [call]}}]}),
                     httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'Ngày 22/10/2026 bán 3 đồ uống, ước tính 75.000 VND.'}}]})]
        with patch('assistant.settings', return_value=('fake-test-key', 'openrouter/free')), patch('httpx.AsyncClient.post', side_effect=responses) as post:
            response = self.client.post('/api/assistant/chat', json={'message': 'Doanh số ngày 22/10/2026?'})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['sources'][0]['data']['summary']['drinks'], 3)
        self.assertEqual(data['sources'][0]['data']['summary']['revenue_vnd'], 75000)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), before)


    def approved_for_reversal(self, quantity=3, opening=1000):
        self.config.stock_items[0].opening_stock = opening
        config = write_config(self.config, initial_import=True)
        draft = self.post_photo(quantity=quantity).json()
        consumption = self.consumption_preview(draft)
        response = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(consumption))
        self.assertEqual(response.status_code, 200, response.text)
        return config, response.json()['report']

    def reversal_body(self, report, **changes):
        preview = self.client.get('/api/sales/reports/' + report['id'] + '/reversal-preview')
        self.assertEqual(preview.status_code, 200, preview.text)
        return {'request_id': str(uuid4()), 'revision': report['revision'],
                'preview_token': preview.json()['preview_token'], 'reason': 'Wrong quantity approved',
                'confirm_stock_impact': True, 'create_correction_draft': True, **changes}

    def test_reversal_restores_original_deductions_and_correction_uses_new_recipes(self):
        config, report = self.approved_for_reversal()
        frozen = report['approval']
        config.preparations[0].output_quantity = 1000
        write_config(config)
        body = self.reversal_body(report)
        response = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['report']['status'], 'reversed')
        self.assertEqual(data['report']['approval'], frozen)
        self.assertEqual(data['report']['items'], report['items'])
        self.assertEqual(data['reversal']['snapshot']['restored_items'][0]['quantity'], '120.000000')
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 1000)
        correction = data['correction_draft']
        self.assertEqual(correction['replaces_report_id'], report['id'])
        self.assertEqual(correction['date'], report['date'])
        self.assertEqual(correction['status'], 'draft')
        self.assertEqual(correction['items'][0]['quantity'], 3)
        self.assertNotEqual(correction['items'][0]['id'], report['items'][0]['id'])
        self.assertEqual(self.client.get(correction['images'][0]['url']).content, self.client.get(report['images'][0]['url']).content)
        correction['items'][0]['quantity'] = 2
        edit = {'revision': correction['revision'], 'date': correction['date'], 'items': [
            {k: row[k] for k in ('id', 'raw_item_name', 'product_id', 'quantity')} for row in correction['items']]}
        changed = self.client.put('/api/sales/reports/' + correction['id'], json=edit)
        self.assertEqual(changed.status_code, 200, changed.text)
        preview = self.consumption_preview(changed.json())
        response = self.client.post('/api/sales/reports/' + correction['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 840)
        dashboard = self.client.get('/api/analytics/dashboard?date_from=2026-10-22&date_to=2026-10-22').json()
        self.assertEqual(dashboard['summary']['drinks'], 2)
        self.assertEqual(dashboard['summary']['revenue_vnd'], 50000)
        self.assertEqual(dashboard['daily'][0]['report_id'], correction['id'])
        self.assertEqual(float(dashboard['ingredient_usage'][0]['quantity']), 160)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='reversal'"), 1)

    def test_reversal_retry_is_safe_and_original_cannot_be_changed(self):
        _, report = self.approved_for_reversal()
        body = self.reversal_body(report)
        url = '/api/sales/reports/' + report['id']
        first = self.client.post(url + '/reverse', json=body)
        self.assertEqual(first.status_code, 200, first.text)
        retry = self.client.post(url + '/reverse', json=body)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()['already_applied'])
        self.assertEqual(retry.json()['correction_draft']['id'], first.json()['correction_draft']['id'])
        self.assertEqual(self.client.post(url + '/reverse', json={**body, 'request_id': str(uuid4())}).status_code, 409)
        self.assertEqual(self.client.post(url + '/reverse', json={**body, 'reason': 'Changed'}).status_code, 409)
        self.assertEqual(self.client.delete(url + '?revision=' + str(first.json()['report']['revision'])).status_code, 409)
        self.assertEqual(self.client.post(url + '/match', json={'revision': first.json()['report']['revision']}).status_code, 409)
        self.assertEqual(self.client.post(url + '/approve', json=self.approval_body(report['approval'], preview_token=report['approval']['reviewed_preview_token'])).status_code, 409)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='reversal'"), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reversals'), 1)

    def test_reversal_requires_reason_confirmation_and_fresh_preview(self):
        _, report = self.approved_for_reversal()
        body = self.reversal_body(report)
        url = '/api/sales/reports/' + report['id'] + '/reverse'
        for changes, code in (({'reason': '   '}, 422), ({'confirm_stock_impact': False}, 400), ({'revision': 0}, 409)):
            self.assertEqual(self.client.post(url, json={**body, **changes}).status_code, code)
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO inventory_movements(id,item_id,kind,signed_quantity) VALUES ('new-stock','powder','correction',1)"))
        self.assertEqual(self.client.post(url, json=body).status_code, 409)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='reversal'"), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 1)

    def test_reversal_failure_rolls_back_stock_status_audit_and_correction(self):
        from sqlalchemy.exc import SQLAlchemyError
        _, report = self.approved_for_reversal()
        body = self.reversal_body(report)
        with patch('reversals.details', side_effect=SQLAlchemyError('Failure after all writes')):
            response = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=body)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='reversal'"), 0)
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 880)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reversals'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 1)
        self.assertEqual(self.scalar('SELECT status FROM sales_reports'), 'approved')

    def test_reversal_concurrent_retries_restore_stock_once(self):
        from concurrent.futures import ThreadPoolExecutor
        _, report = self.approved_for_reversal()
        body = self.reversal_body(report)
        def reverse(_):
            return TestClient(app.app).post('/api/sales/reports/' + report['id'] + '/reverse', json=body).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(reverse, range(2)))
        self.assertEqual(statuses, [200, 200])
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 1000)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='reversal'"), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 2)

    def test_reversal_correction_photos_survive_draft_deletion_and_can_be_uploaded_again(self):
        _, report = self.approved_for_reversal()
        response = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=self.reversal_body(report))
        self.assertEqual(response.status_code, 200, response.text)
        correction = response.json()['correction_draft']
        self.assertEqual(self.post_photo().status_code, 409)  # Its correction already owns an active copy.
        deleted = self.client.delete('/api/sales/reports/' + correction['id'] + '?revision=' + str(correction['revision']))
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get(report['images'][0]['url']).status_code, 200)
        self.assertIsNone(self.client.get('/api/sales/reports/' + report['id']).json()['reversal']['replacement_report_id'])
        uploaded = self.post_photo()
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertNotEqual(uploaded.json()['id'], report['id'])

    def test_reversal_physical_count_warning_and_recorded_activity(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        _, report = self.approved_for_reversal()
        count = self.client.post('/api/inventory/counts', json=self.stock_entry(actual_quantity='800'))
        self.assertEqual(count.status_code, 201, count.text)
        preview = self.client.get('/api/sales/reports/' + report['id'] + '/reversal-preview').json()
        self.assertTrue(preview['restored_items'][0]['counted_after_approval'])
        self.assertEqual(preview['restored_items'][0]['balance_after'], '920.000000')
        self.assertTrue(any('physically counted' in warning for warning in preview['warnings']))
        response = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=self.reversal_body(report))
        self.assertEqual(response.status_code, 200, response.text)
        history = self.client.get('/api/inventory/history').json()['movements']
        reversal = next(row for row in history if row['kind'] == 'reversal')
        self.assertTrue(reversal['reversal_of'])
        self.assertEqual(reversal['report_id'], report['id'])
        today = datetime.now(ZoneInfo('Asia/Singapore')).date().isoformat()
        activity = self.client.get('/api/analytics/dashboard?date_from=' + today + '&date_to=' + today).json()['inventory_activity']
        powder = next(row for row in activity if row['item_id'] == 'powder')
        self.assertEqual(powder['reversal_quantity'], '120.000000')
        self.assertEqual(powder['correction_added'], '0.000000')

    def test_zero_sale_reversal_and_unknown_stock_are_not_invented(self):
        _, report = self.approved_for_reversal(quantity=0, opening=None)
        response = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=self.reversal_body(report, create_correction_draft=False))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['reversal']['snapshot']['movement_count'], 0)
        self.assertIsNone(response.json()['correction_draft'])
        self.assertFalse(self.stock_state()['balance_known'])
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), 0)
        dashboard = self.client.get('/api/analytics/dashboard?date_from=2026-10-22&date_to=2026-10-22').json()
        self.assertEqual(dashboard['daily'][0]['status'], 'reversed')
        self.assertIsNone(dashboard['summary']['drinks'])
        self.assertEqual(self.post_photo().status_code, 201)

    def test_two_rounds_of_correction_preserve_each_original_and_only_one_active_date(self):
        _, report = self.approved_for_reversal()
        first = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=self.reversal_body(report)).json()
        correction = first['correction_draft']
        preview = self.consumption_preview(correction)
        approved = self.client.post('/api/sales/reports/' + correction['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(approved.status_code, 200, approved.text)
        second = self.client.post('/api/sales/reports/' + correction['id'] + '/reverse', json=self.reversal_body(approved.json()['report']))
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(self.scalar("SELECT count(*) FROM sales_reports WHERE status='reversed'"), 2)
        self.assertEqual(self.scalar("SELECT count(*) FROM sales_reports WHERE status!='reversed'"), 1)
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 1000)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reversals'), 2)
        dashboard = self.client.get('/api/analytics/dashboard?date_from=2026-10-22&date_to=2026-10-22').json()
        self.assertEqual(dashboard['daily'][0]['status'], 'draft')
        self.assertEqual(dashboard['daily'][0]['report_id'], second.json()['correction_draft']['id'])


    def test_nonzero_reversal_preserves_unknown_opening_balance(self):
        _, report = self.approved_for_reversal(opening=None)
        response = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=self.reversal_body(report))
        self.assertEqual(response.status_code, 200, response.text)
        stock = self.stock_state()
        self.assertFalse(stock['balance_known'])
        self.assertIsNone(stock['quantity'])
        self.assertEqual(stock['ledger_estimate'], '0.000000')

    def test_reversal_can_restore_archived_ingredient_without_current_recipe(self):
        _, report = self.approved_for_reversal()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE stock_items SET is_deleted=true WHERE id='powder'"))
            conn.execute(text("UPDATE products SET active=false WHERE id='drink'"))
        response = self.client.get('/api/sales/reports/' + report['id'] + '/reversal-preview')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['restored_items'][0]['archived_item'])
        response = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=self.reversal_body(report, create_correction_draft=False))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.scalar("SELECT sum(signed_quantity) FROM inventory_movements WHERE item_id='powder'"), 1000)

    def test_zero_delta_count_also_invalidates_reversal_preview(self):
        _, report = self.approved_for_reversal()
        body = self.reversal_body(report)
        counted = self.client.post('/api/inventory/counts', json=self.stock_entry(actual_quantity='880'))
        self.assertEqual(counted.status_code, 201, counted.text)
        response = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=body)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='reversal'"), 0)

    def test_duplicate_photo_across_dates_is_still_serialized(self):
        from concurrent.futures import ThreadPoolExecutor
        write_config(self.config, initial_import=True)
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4), 'red').save(buffer, format='PNG')
        image = buffer.getvalue()
        def upload(day):
            extraction = {'date': day, 'items': [{'item_name': 'Tea M', 'quantity': 1, 'issues': []}], 'issues': []}
            return TestClient(app.app).post('/api/sales/drafts', data={'report': json.dumps(extraction), 'report_date': day},
                        files={'image': ('photo.png', image, 'image/png')}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(upload, ['2026-10-22', '2026-10-23']))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_images'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 1)


if __name__ == '__main__':
    unittest.main()
