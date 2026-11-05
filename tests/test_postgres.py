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
from sqlalchemy.exc import SQLAlchemyError
from fastapi.testclient import TestClient
from auth_support import authenticated_client
from PIL import Image

import app

from bobaops.core.database import database_url
from bobaops.catalog.repository import StaleRevision, read_config, write_config
from bobaops.catalog.schemas import ShopConfig, StockItem
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
        for module in ('bobaops.catalog.repository', 'bobaops.sales.routes', 'bobaops.inventory.routes', 'scripts.import_confirmed_catalog'):
            for name, value in [('get_engine', self.engine), ('get_tables', metadata.tables)]:
                mocked = patch(module + '.' + name, return_value=value)
                mocked.start()
                self.addCleanup(mocked.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        mocked = patch('bobaops.sales.routes.IMAGE_DIR', Path(temp.name))
        mocked.start()
        self.addCleanup(mocked.stop)
        for name in ('bobaops.sales.routes.storage_backend', 'bobaops.inventory.routes.storage_backend'):
            mocked = patch(name, return_value='postgres')
            mocked.start()
            self.addCleanup(mocked.stop)
        self.client = authenticated_client(self, app.app)
        mocked = patch('bobaops.core.photos.environment', return_value={'RECEIPT_STORAGE': 'local'})
        mocked.start()
        self.addCleanup(mocked.stop)
        self.config = ShopConfig.model_validate(sample())

    def test_cloud_receipt_upload_read_duplicate_and_delete(self):
        from test_photos import remote_storage
        fake = remote_storage(self)
        write_config(self.config, initial_import=True)
        response = self.post_photo()
        self.assertEqual(response.status_code, 201, response.text)
        draft = response.json()
        self.assertEqual(len(fake.objects), 1)
        self.assertTrue(self.scalar('SELECT source_path FROM sales_images').startswith('supabase:receipts/'))
        self.assertEqual(self.client.get(draft['images'][0]['url']).status_code, 200)
        self.assertEqual(TestClient(app.app).get(draft['images'][0]['url']).status_code, 401)
        self.assertEqual(self.post_photo().status_code, 409)
        self.assertEqual(len(fake.objects), 1)
        result = self.client.delete('/api/sales/reports/' + draft['id'] + '?revision=' + str(draft['revision']))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()['photo_cleanup_pending'])
        self.assertFalse(fake.objects)

    def test_cloud_copy_preserves_current_data_and_refuses_overwrite(self):
        from scripts.prepare_cloud import copy_shop
        # Include generated identity IDs, version records, and a non-zero current balance.
        self.config.stock_items[0].opening_stock = 1000
        saved = write_config(self.config, initial_import=True)
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO product_aliases (product_id,name) VALUES ('drink','Alias one')"))
        target_schema = self.schema + '_copy'
        with self.admin.begin() as c:
            c.execute(text('CREATE SCHEMA ' + target_schema))
        def cleanup():
            with self.admin.begin() as c:
                c.execute(text('DROP SCHEMA ' + target_schema + ' CASCADE'))
        self.addCleanup(cleanup)
        target = create_engine(self.engine.url, connect_args={'options': '-csearch_path=' + target_schema})
        self.addCleanup(target.dispose)
        with target.begin() as c:
            from alembic import command
            from alembic.config import Config
            cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
            cfg.attributes['connection'] = c
            command.upgrade(cfg, 'head')
            self.assertEqual(c.scalar(text('SELECT version_num FROM alembic_version')), '0008')
        with self.engine.connect() as source, target.begin() as dest:
            result = copy_shop(source, dest)
            self.assertEqual(result['products'], 1)
            self.assertEqual(dest.scalar(text("SELECT quantity FROM inventory_balances WHERE item_id='powder'")), 1000)
            self.assertEqual(dest.scalar(text('SELECT revision FROM shop_state')), saved.revision)
            # The target sequence must advance past the explicitly copied alias ID.
            dest.execute(text("INSERT INTO product_aliases (product_id,name) VALUES ('drink','Alias two')"))
            self.assertEqual(dest.scalar(text('SELECT count(*) FROM product_aliases')), 2)
            self.assertEqual(dest.scalar(text('SELECT count(*) FROM auth_sessions')), 0)
        with self.engine.connect() as source, target.begin() as dest:
            with self.assertRaises(ValueError):
                copy_shop(source, dest)

    def test_cloud_upload_is_cleaned_up_after_database_rollback(self):
        from test_photos import remote_storage
        fake = remote_storage(self)
        write_config(self.config, initial_import=True)
        with patch('bobaops.sales.routes.details', side_effect=SQLAlchemyError('simulated failure')):
            self.assertEqual(self.post_photo().status_code, 503)
        self.assertFalse(fake.objects)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 0)

    def test_cloud_correction_draft_keeps_photo_referenced_by_original(self):
        from test_photos import remote_storage
        fake = remote_storage(self)
        config, report = self.approved_for_reversal()
        result = self.client.post('/api/sales/reports/' + report['id'] + '/reverse', json=self.reversal_body(report))
        self.assertEqual(result.status_code, 200, result.text)
        correction = self.client.get('/api/sales/reports').json()
        draft = next(r for r in correction if r['status'] == 'draft')
        full = self.client.get('/api/sales/reports/' + draft['id']).json()
        deleted = self.client.delete('/api/sales/reports/' + draft['id'] + '?revision=' + str(full['revision']))
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(len(fake.objects), 1)
        original = self.client.get('/api/sales/reports/' + report['id']).json()
        self.assertEqual(self.client.get(original['images'][0]['url']).status_code, 200)

    def drop_schema(self):
        with self.admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {self.schema} CASCADE'))

    def scalar(self, sql):
        with self.engine.connect() as conn:
            return conn.scalar(text(sql))

    def test_auth_sessions_are_persistent_revocable_and_bound_to_credentials(self):
        from dataclasses import replace
        import secrets
        from auth_support import TEST_SETTINGS
        from bobaops.auth.store import PostgresStore
        from bobaops.auth.security import session_digest
        database = PostgresStore(self.engine)
        sid = secrets.token_urlsafe(32)
        database.create(sid, TEST_SETTINGS)
        another_worker = PostgresStore(self.engine)
        self.assertTrue(another_worker.valid(sid, TEST_SETTINGS))
        self.assertFalse(another_worker.valid(sid, replace(TEST_SETTINGS, username='new-owner')))
        with self.engine.connect() as conn:
            token = conn.scalar(text('SELECT token_hash FROM auth_sessions'))
        self.assertEqual(token, session_digest(sid))
        self.assertNotEqual(token, sid)
        another_worker.revoke(sid)
        self.assertFalse(database.valid(sid, TEST_SETTINGS))
        sid2 = secrets.token_urlsafe(32)
        database.create(sid2, TEST_SETTINGS)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE auth_sessions SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'"))
        self.assertFalse(database.valid(sid2, TEST_SETTINGS))

    def test_auth_login_limits_are_atomic_across_workers_and_expire(self):
        from concurrent.futures import ThreadPoolExecutor
        from bobaops.auth.store import PostgresStore
        database = PostgresStore(self.engine)
        def attempt(_):
            return PostgresStore(self.engine).reserve_attempt('a' * 64)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        self.assertEqual(results.count(0), 5)
        self.assertEqual(sum(value > 0 for value in results), 1)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE auth_login_limits SET window_started_at = CURRENT_TIMESTAMP - INTERVAL '16 minutes'"))
        self.assertEqual(database.reserve_attempt('a' * 64), 0)
        self.assertEqual(self.scalar('SELECT attempts FROM auth_login_limits'), 1)
        database.clear_attempts('a' * 64)
        self.assertEqual(self.scalar('SELECT count(*) FROM auth_login_limits'), 0)

    def test_auth_login_and_logout_use_the_real_postgres_session_store(self):
        from auth_support import TEST_PASSWORD
        from bobaops.auth import store
        from bobaops.auth.security import COOKIE
        with patch.object(store, 'get_store', return_value=store.PostgresStore(self.engine)):
            with TestClient(app.app) as client:
                token = client.get('/api/auth/login').json()['csrf_token']
                response = client.post('/api/auth/login', headers={'X-CSRF-Token': token},
                                       json={'username': 'test-owner', 'password': TEST_PASSWORD})
                self.assertEqual(response.status_code, 200)
                saved_cookie = client.cookies.get(COOKIE)
                session = client.get('/api/auth/session')
                self.assertEqual(session.status_code, 200)
                csrf = session.json()['csrf_token']
                self.assertEqual(self.scalar('SELECT count(*) FROM auth_sessions'), 1)
                self.assertEqual(client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf}).status_code, 200)
                client.cookies.set(COOKIE, saved_cookie)
                self.assertEqual(client.get('/api/auth/session').status_code, 401)
                self.assertEqual(self.scalar('SELECT count(*) FROM auth_sessions WHERE revoked_at IS NOT NULL'), 1)

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
        from bobaops.sales import routes as sales
        from sqlalchemy.exc import SQLAlchemyError
        write_config(self.config, initial_import=True)
        with patch('bobaops.sales.routes.details', side_effect=SQLAlchemyError('simulated failure')):
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
        from bobaops.sales import routes as sales
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
        with patch('bobaops.sales.routes.details', side_effect=SQLAlchemyError('simulated failure after movement writes')):
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
            return TestClient(app.app, cookies=self.client.cookies, headers=self.client.headers).post(url, json=body).status_code
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
            return TestClient(app.app, cookies=self.client.cookies, headers=self.client.headers).post('/api/inventory/purchases', json=data).status_code
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
        from bobaops.assistant import routes as assistant
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
        with patch('bobaops.assistant.routes.settings', return_value=('fake-test-key', 'openrouter/free')), patch('httpx.AsyncClient.post', side_effect=responses) as post:
            response = self.client.post('/api/assistant/chat', json={'message': 'Doanh số ngày 22/10/2026?'})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['sources'][0]['data']['summary']['drinks'], 3)
        self.assertEqual(data['sources'][0]['data']['summary']['revenue_vnd'], 75000)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(self.scalar('SELECT count(*) FROM inventory_movements'), before)

    def test_complete_receipt_workflow_and_reversal(self):
        """One receipt travels through the real APIs/SQL; only model transport is mocked."""
        import httpx
        self.config.stock_items[0].opening_stock = 1000
        write_config(self.config, initial_import=True)
        image = io.BytesIO()
        Image.new('RGB', (8, 8), 'green').save(image, format='PNG')
        extracted = {'date': '2026-10-22', 'items': [
            {'item_name': 'Tea M', 'quantity': 3, 'issues': []}], 'issues': []}
        reply = httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(extracted)}]}}]})
        with patch('bobaops.extraction.routes.settings', return_value=('fake-key', 'test-model')), \
                patch('httpx.AsyncClient.post', return_value=reply) as provider:
            response = self.client.post('/api/extract', files={'image': ('receipt.png', image.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        provider.assert_called_once()
        form = {'report': json.dumps(response.json()), 'report_date': extracted['date']}
        def save():
            return self.client.post('/api/sales/drafts', data=form,
                                    files={'image': ('receipt.png', image.getvalue(), 'image/png')})
        saved = save()
        self.assertEqual(saved.status_code, 201, saved.text)
        report = saved.json()
        self.assertEqual(report['items'][0]['product_id'], 'drink')
        self.assertEqual(save().status_code, 409)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 1)
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 1000)
        url = '/api/sales/reports/' + report['id']
        preview = self.consumption_preview(report)
        approved = self.client.post(url + '/approve', json=self.approval_body(preview))
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 880)
        self.assertEqual(self.client.post(url + '/approve', json=self.approval_body(preview)).status_code, 409)
        query = '?date_from=2026-10-22&date_to=2026-10-22'
        summary = self.client.get('/api/analytics/dashboard' + query).json()['summary']
        self.assertEqual(summary['drinks'], 3)
        self.assertEqual(summary['revenue_vnd'], 75000)
        report = approved.json()['report']
        reversed_report = self.client.post(url + '/reverse', json=self.reversal_body(report))
        self.assertEqual(reversed_report.status_code, 200, reversed_report.text)
        self.assertEqual(self.scalar("SELECT quantity FROM inventory_balances WHERE item_id='powder'"), 1000)
        self.assertEqual(self.client.get('/api/analytics/dashboard' + query).json()['summary']['approved_reports'], 0)


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
        with patch('bobaops.sales.reversals.details', side_effect=SQLAlchemyError('Failure after all writes')):
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
            return TestClient(app.app, cookies=self.client.cookies, headers=self.client.headers).post('/api/sales/reports/' + report['id'] + '/reverse', json=body).status_code
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
            return TestClient(app.app, cookies=self.client.cookies, headers=self.client.headers).post('/api/sales/drafts', data={'report': json.dumps(extraction), 'report_date': day},
                        files={'image': ('photo.png', image, 'image/png')}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(upload, ['2026-10-22', '2026-10-23']))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_images'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'), 1)


    def ice_cream_fixture(self, manual=True):
        from bobaops.catalog.schemas import Component
        self.config.stock_items[0].ice_cream_powder = True
        self.config.stock_items[0].opening_stock = 5000
        self.config.stock_items[1].name = 'Kem'
        self.config.stock_items[1].unit = 'g'
        self.config.stock_items += [
            StockItem(id='matcha', name='Matcha powder', unit='g', kind='raw', opening_stock=3000, ice_cream_powder=True),
            StockItem(id='cup', name='Cup', unit='piece', kind='packaging', opening_stock=100),
            StockItem(id='oreo', name='Oreo', unit='g', kind='raw', opening_stock=1000)]
        self.config.products[0].components += [Component(item_id='cup', quantity=1), Component(item_id='oreo', quantity=8)]
        self.config.preparations[0].deduction_mode = 'manual_ice_cream' if manual else 'automatic'
        return write_config(self.config, initial_import=True)

    def ice_cream_body(self, quantity='1500', item='powder', day='2026-10-22'):
        response = self.client.get('/api/inventory/ice-cream-usage', params={'used_on': day})
        self.assertEqual(response.status_code, 200, response.text)
        stock = next(r for r in response.json()['items'] if r['id'] == item)
        return {'request_id': str(uuid4()), 'used_on': day, 'note': 'Powders actually used for ice cream',
                'items': [{'item_id': item, 'total_used': quantity, 'usage_token': stock['usage_token'],
                           'balance_token': stock['balance_token']}]}

    def test_daily_ice_cream_totals_adjust_difference_and_are_retry_safe(self):
        self.ice_cream_fixture()
        body = self.ice_cream_body()
        response = self.client.post('/api/inventory/ice-cream-usage', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.stock_state()['quantity'], '3500.000000')
        replay = self.client.post('/api/inventory/ice-cream-usage', json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()['replayed'])
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='ice_cream_usage'"), 1)
        for total, expected in [('2000', '3000.000000'), ('2000', '3000.000000'), ('0', '5000.000000')]:
            result = self.client.post('/api/inventory/ice-cream-usage', json=self.ice_cream_body(total))
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.stock_state()['quantity'], expected)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='ice_cream_usage'"), 3)
        row = next(r for r in self.client.get('/api/inventory/ice-cream-usage?used_on=2026-10-22').json()['items'] if r['id'] == 'powder')
        self.assertTrue(row['recorded'])
        self.assertEqual(self.scalar('SELECT quantity FROM ice_cream_daily_usage'), 0)

    def test_manual_ice_cream_sales_keep_cups_toppings_and_reversal_keeps_powder_usage(self):
        self.ice_cream_fixture()
        result = self.client.post('/api/inventory/ice-cream-usage', json=self.ice_cream_body('1500'))
        self.assertEqual(result.status_code, 200, result.text)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        self.assertTrue(preview['can_approve'], preview)
        self.assertEqual({r['item_id'] for r in preview['consumption']}, {'cup', 'oreo'})
        self.assertTrue(preview['manual_ice_cream'])
        approval = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(approval.status_code, 200, approval.text)
        self.assertEqual(self.stock_state()['quantity'], '3500.000000')
        self.assertEqual(self.stock_state('cup')['quantity'], '97.000000')
        self.assertEqual(self.stock_state('oreo')['quantity'], '976.000000')
        dashboard = self.client.get('/api/analytics/dashboard?date_from=2026-10-22&date_to=2026-10-22').json()
        powder = next(r for r in dashboard['ingredient_usage'] if r['item_id'] == 'powder')
        self.assertEqual(powder['quantity'], '1500.000000')
        self.assertEqual(powder['sales_quantity'], '0.000000')
        approved = approval.json()['report']
        reverse = self.client.post('/api/sales/reports/' + draft['id'] + '/reverse', json=self.reversal_body(approved))
        self.assertEqual(reverse.status_code, 200, reverse.text)
        self.assertEqual(self.stock_state()['quantity'], '3500.000000')
        self.assertEqual(self.stock_state('cup')['quantity'], '100.000000')
        self.assertEqual(self.scalar('SELECT quantity FROM ice_cream_daily_usage'), 1500)

    def test_ice_cream_shared_powder_is_still_automatic_in_drinks(self):
        from bobaops.catalog.schemas import Component, Preparation
        saved = self.ice_cream_fixture()
        saved.stock_items.append(StockItem(id='concentrate', name='Drink concentrate', unit='ml', kind='prepared'))
        saved.preparations.append(Preparation(id='drink-prep', name='Prepare drink concentrate', output_item_id='concentrate',
            output_quantity=2000, inputs=[Component(item_id='matcha', quantity=500)]))
        saved.products[0].components.append(Component(item_id='concentrate', quantity=160))
        write_config(saved)
        posted = self.client.post('/api/inventory/ice-cream-usage', json=self.ice_cream_body('1000', item='matcha'))
        self.assertEqual(posted.status_code, 200, posted.text)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        use = {r['item_id']: float(r['quantity']) for r in preview['consumption']}
        self.assertNotIn('powder', use)
        self.assertEqual(use['matcha'], 120)
        approved = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(self.stock_state('matcha')['quantity'], '1880.000000')
        dashboard = self.client.get('/api/analytics/dashboard?date_from=2026-10-22&date_to=2026-10-22').json()
        powder = next(r for r in dashboard['ingredient_usage'] if r['item_id'] == 'matcha')
        self.assertEqual(powder['sales_quantity'], '120.000000')
        self.assertEqual(powder['ice_cream_quantity'], '1000.000000')
        self.assertEqual(powder['quantity'], '1120.000000')

    def test_ice_cream_legacy_automatic_reports_block_double_deduction(self):
        saved = self.ice_cream_fixture(manual=False)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        approved = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(approved.status_code, 200, approved.text)
        saved.preparations[0].deduction_mode = 'manual_ice_cream'
        write_config(saved)
        before = self.stock_state()['quantity']
        result = self.client.post('/api/inventory/ice-cream-usage', json=self.ice_cream_body('1500'))
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.stock_state()['quantity'], before)
        self.assertEqual(self.scalar('SELECT count(*) FROM ice_cream_daily_usage'), 0)

    def test_ice_cream_usage_blocks_switching_back_to_automatic_sales(self):
        saved = self.ice_cream_fixture()
        result = self.client.post('/api/inventory/ice-cream-usage', json=self.ice_cream_body())
        self.assertEqual(result.status_code, 200, result.text)
        saved.preparations[0].deduction_mode = 'automatic'
        write_config(saved)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        self.assertFalse(preview['can_approve'])
        response = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'"), 0)

    def test_ice_cream_usage_rejects_stale_duplicate_and_unflagged_rows(self):
        self.ice_cream_fixture()
        body = self.ice_cream_body()
        response = self.client.post('/api/inventory/ice-cream-usage', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post('/api/inventory/ice-cream-usage', json={**body, 'request_id': str(uuid4())}).status_code, 409)
        self.assertEqual(self.client.post('/api/inventory/ice-cream-usage', json={**body, 'note': 'Changed'}).status_code, 409)
        invalid = self.ice_cream_body('10')
        invalid['items'][0]['item_id'] = 'oreo'
        self.assertEqual(self.client.post('/api/inventory/ice-cream-usage', json=invalid).status_code, 400)
        duplicate = self.ice_cream_body()
        duplicate['items'] *= 2
        self.assertEqual(self.client.post('/api/inventory/ice-cream-usage', json=duplicate).status_code, 422)
        invalid = self.ice_cream_body('-1')
        self.assertEqual(self.client.post('/api/inventory/ice-cream-usage', json=invalid).status_code, 422)

    def test_ice_cream_usage_concurrent_retries_and_atomic_failure(self):
        from concurrent.futures import ThreadPoolExecutor
        self.ice_cream_fixture()
        body = self.ice_cream_body()
        def save(_):
            return TestClient(app.app, cookies=self.client.cookies, headers=self.client.headers).post('/api/inventory/ice-cream-usage', json=body).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(save, range(2))), [200, 200])
        self.assertEqual(self.stock_state()['quantity'], '3500.000000')
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='ice_cream_usage'"), 1)
        old = self.ice_cream_body('2000')
        second = self.ice_cream_body('500', item='matcha')['items'][0]
        second['usage_token'] = '0' * 64
        old['items'].append(second)
        response = self.client.post('/api/inventory/ice-cream-usage', json=old)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.stock_state()['quantity'], '3500.000000')
        self.assertEqual(self.stock_state('matcha')['quantity'], '3000.000000')

    def test_ice_cream_modes_roundtrip_and_versions_remain_frozen(self):
        saved = self.ice_cream_fixture(manual=False)
        self.assertEqual(read_config().preparations[0].deduction_mode, 'automatic')
        self.assertTrue(next(s for s in read_config().stock_items if s.id == 'powder').ice_cream_powder)
        saved.preparations[0].deduction_mode = 'manual_ice_cream'
        write_config(saved)
        self.assertEqual(read_config().preparations[0].deduction_mode, 'manual_ice_cream')
        self.assertEqual(self.scalar("SELECT deduction_mode FROM preparation_versions WHERE version=1"), 'automatic')
        self.assertEqual(self.scalar("SELECT deduction_mode FROM preparation_versions WHERE version=2"), 'manual_ice_cream')


    def test_confirmed_catalog_import_is_atomic_retry_safe_and_preserves_history(self):
        from test_catalog_import import fixture
        from scripts.import_confirmed_catalog import compile_catalog, apply_catalog
        self.config.stock_items[0].opening_stock = 5000
        write_config(self.config, initial_import=True)
        draft = self.post_photo().json()
        preview = self.consumption_preview(draft)
        approved = self.client.post('/api/sales/reports/' + draft['id'] + '/approve', json=self.approval_body(preview))
        self.assertEqual(approved.status_code, 200, approved.text)
        snapshot_before = self.scalar("SELECT approval_snapshot::text FROM sales_reports WHERE status='approved'")
        movements_before = self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'")
        plan = compile_catalog(fixture(), read_config())
        result = apply_catalog(plan)
        self.assertFalse(result['replayed'])
        self.assertEqual(len(read_config().products), 6)
        self.assertEqual(self.scalar("SELECT count(*) FROM stock_counts"), 13)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'"), movements_before)
        self.assertEqual(self.scalar("SELECT approval_snapshot::text FROM sales_reports WHERE status='approved'"), snapshot_before)
        self.assertTrue(apply_catalog(plan)['replayed'])
        self.assertEqual(self.scalar("SELECT count(*) FROM stock_counts"), 13)
        self.assertEqual(self.scalar("SELECT count(*) FROM catalog_imports"), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM products WHERE id='drink' AND is_deleted"), 1)
        # A failure halfway through another import also rolls back the catalog revision.
        revision = read_config().revision
        altered = fixture()
        altered['demo']['items'][0]['opening_stock'] = 6000
        bad = compile_catalog(altered, read_config())
        list(bad['inventory'].values())[1]['quantity'] = -1
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            apply_catalog(bad)
        self.assertEqual(read_config().revision, revision)
        self.assertEqual(self.scalar("SELECT count(*) FROM stock_counts"), 13)
        self.assertEqual(self.scalar("SELECT count(*) FROM catalog_imports"), 1)

    def test_imported_menu_full_receipt_flow_and_manual_powders(self):
        from test_catalog_import import fixture, empty
        from scripts.import_confirmed_catalog import compile_catalog, apply_catalog
        plan = compile_catalog(fixture(), empty())
        apply_catalog(plan)
        config = read_config()
        byname = {x.name:x for x in config.stock_items}
        image = io.BytesIO()
        Image.new('RGB',(4,4),'yellow').save(image,format='PNG')
        report = {'date':'2026-10-30','issues':[], 'items':[{'item_name':(p.name.removeprefix('Thêm ') if p.category=='topping' else p.name)+' (MT)','quantity':2,'issues':[]} for p in config.products]}
        response = self.client.post('/api/sales/drafts',data={'report':json.dumps(report),'report_date':'2026-10-30'},
            files={'image':('demo.png',image.getvalue(),'image/png')})
        self.assertEqual(response.status_code,201,response.text)
        draft = response.json()
        self.assertTrue(all(x['product_id'] for x in draft['items']))
        preview = self.consumption_preview(draft)
        self.assertTrue(preview['can_approve'],preview)
        usage = {x['item_id']:x for x in preview['consumption']}
        self.assertNotIn(byname['Bột kem nguyên vị'].id,usage)
        self.assertNotIn(byname['Bột matcha'].id,usage)
        self.assertEqual(float(usage[byname['Ốc quế'].id]['quantity']),2)
        self.assertEqual(float(usage[byname['Cốc U 400 ml'].id]['quantity']),4)
        self.assertEqual(float(usage[byname['Thạch dừa'].id]['quantity']),450)
        response = self.client.post('/api/sales/reports/'+draft['id']+'/approve',json=self.approval_body(preview))
        self.assertEqual(response.status_code,200,response.text)
        inventory = self.client.get('/api/inventory').json()['items']
        self.assertTrue(all(x['balance_known'] for x in inventory))
        powder = next(x for x in inventory if x['id']==byname['Bột kem nguyên vị'].id)
        self.assertEqual(float(powder['quantity']),5000)
        daily = self.client.get('/api/inventory/ice-cream-usage?used_on=2026-10-30').json()
        self.assertEqual(len(daily['items']),2)
        # Imported usage and recipes also feed the read-only analytics / assistant tools.
        dashboard = self.client.get('/api/analytics/dashboard?date_from=2026-10-30&date_to=2026-10-30')
        self.assertEqual(dashboard.status_code,200,dashboard.text)
        self.assertEqual(dashboard.json()['summary']['approved_reports'],1)
        from bobaops.assistant import routes as assistant
        recipes = assistant.lookup('read_recipes',assistant.SearchArgs(search='Kem Ốc Quế',limit=10))
        self.assertTrue(recipes)


    def test_nlp_upload_suggestions_manual_review_and_approval(self):
        self.config.stock_items[0].opening_stock = 5000
        product = self.config.products[0]
        product.name = 'Dương Chi Cam Lộ'
        product.size = 'M'
        for identifier, name in [('pure','Super Sundae O-coco Matcha (Kem Matcha)'),
                                 ('mix','Super Sundae O-coco Matcha (Kem Mix)')]:
            clone = product.model_copy(deep=True)
            clone.id = identifier
            clone.name = name
            clone.category = 'sundae'
            clone.size = None
            self.config.products.append(clone)
        write_config(self.config, initial_import=True)
        image = io.BytesIO()
        Image.new('RGB',(4,4),'orange').save(image,format='PNG')
        report = {'date':'2026-10-30','issues':[], 'items':[
            {'item_name':'Dương Chỉ Cam Lộ (MT)','quantity':3,'issues':[]},
            {'item_name':'Super Sundae O-coco Matcha (MT)','quantity':2,'issues':[]}]}
        response = self.client.post('/api/sales/drafts',data={'report':json.dumps(report),'report_date':'2026-10-30'},
            files={'image':('nlp.png',image.getvalue(),'image/png')})
        self.assertEqual(response.status_code,201,response.text)
        draft = response.json()
        first, second = draft['items']
        self.assertEqual(first['product_id'],'drink')
        self.assertEqual(first['raw_item_name'],'Dương Chỉ Cam Lộ (MT)')
        self.assertEqual(first['matching']['method'],'approximate')
        self.assertIsNone(second['product_id'])
        self.assertEqual({x['product_id'] for x in second['matching']['suggestions']},{'pure','mix'})
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='sale'"),0)
        preview = self.consumption_preview(draft)
        self.assertFalse(preview['can_approve'])
        # A reviewed manual choice is preserved by subsequent rematching.
        body = {'revision':draft['revision'],'date':'2026-10-30','items':[
            {k:r[k] for k in ('id','raw_item_name','product_id','quantity')} for r in draft['items']]}
        body['items'][1]['product_id']='mix'
        response = self.client.put('/api/sales/reports/'+draft['id'],json=body)
        self.assertEqual(response.status_code,200,response.text)
        saved = response.json()
        rematched = self.client.post('/api/sales/reports/'+draft['id']+'/match',json={'revision':saved['revision']})
        self.assertEqual(rematched.status_code,200,rematched.text)
        self.assertEqual(rematched.json()['items'][1]['product_id'],'mix')
        preview = self.consumption_preview(rematched.json())
        self.assertTrue(preview['can_approve'],preview)
        approved = self.client.post('/api/sales/reports/'+draft['id']+'/approve',json=self.approval_body(preview))
        self.assertEqual(approved.status_code,200,approved.text)
        self.assertEqual(approved.json()['report']['items'][0]['raw_item_name'],'Dương Chỉ Cam Lộ (MT)')
        self.assertIsNone(approved.json()['report']['items'][0]['matching'])



    def test_receipt_and_daily_powders_commit_atomically_using_receipt_date(self):
        self.ice_cream_fixture()
        def send(body, color='red', day='2026-10-22'):
            image=io.BytesIO()
            Image.new('RGB',(4,4),color).save(image,format='PNG')
            report={'date':day,'issues':[],'items':[{'item_name':'Tea M','quantity':1,'issues':[]}]}
            return self.client.post('/api/sales/drafts',data={'report':json.dumps(report),'report_date':day,'powder_usage':json.dumps(body)},
                                    files={'image':('receipt.png',image.getvalue(),'image/png')})
        stale=self.ice_cream_body('1500')
        stale['items'][0]['usage_token']='0'*64
        response=send(stale)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_reports'),0)
        self.assertEqual(self.scalar('SELECT count(*) FROM ice_cream_daily_usage'),0)
        self.assertEqual(self.stock_state()['quantity'],'5000.000000')
        from bobaops.sales import routes as sales
        self.assertEqual(list(sales.IMAGE_DIR.glob('*')),[])
        valid=self.ice_cream_body('1500')
        response=send(valid)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(self.stock_state()['quantity'],'3500.000000')
        self.assertEqual(send(valid).status_code,409)
        self.assertEqual(self.stock_state()['quantity'],'3500.000000')
        same=send(self.ice_cream_body('1500'),color='blue')
        self.assertEqual(same.status_code,201,same.text)
        self.assertEqual(self.stock_state()['quantity'],'3500.000000')
        changed=send(self.ice_cream_body('2000'),color='green')
        self.assertEqual(changed.status_code,201,changed.text)
        self.assertEqual(self.stock_state()['quantity'],'3000.000000')
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_images'),3)
        mismatch=send(self.ice_cream_body('10',day='2026-10-23'),color='black')
        self.assertEqual(mismatch.status_code,422,mismatch.text)
        self.assertEqual(self.scalar('SELECT count(*) FROM sales_images'),3)
        self.assertEqual(self.scalar("SELECT count(*) FROM inventory_movements WHERE kind='ice_cream_usage'"),2)

    def test_inventory_history_filters_purchase_date_count_waste_and_thresholds(self):
        self.config.stock_items[0].opening_stock=1000
        write_config(self.config,initial_import=True)
        purchase={'request_id':str(uuid4()),'purchased_on':'2026-05-07','note':'Backdated purchase',
                  'items':[{'item_id':'powder','quantity':'100','total_cost_vnd':1000}]}
        self.assertEqual(self.client.post('/api/inventory/purchases',json=purchase).status_code,201)
        count=self.stock_entry(actual_quantity='1100')
        self.assertEqual(self.client.post('/api/inventory/counts/batch',json={'request_id':str(uuid4()),'items':[
            {k:v for k,v in count.items() if k!='request_id'}]}).status_code,201)
        self.assertEqual(self.client.post('/api/inventory/waste',json=self.stock_entry(kind='waste',quantity='10')).status_code,201)
        self.assertEqual(self.client.post('/api/inventory/thresholds',json=self.stock_entry(kind='threshold',threshold='100')).status_code,200)
        self.assertEqual(self.client.post('/api/inventory/thresholds',json=self.stock_entry(kind='threshold',threshold=None)).status_code,200)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE inventory_operations SET created_at='2026-10-22T16:30:00Z'"))
            conn.execute(text("UPDATE inventory_movements SET occurred_at='2026-10-22T16:30:00Z'"))
            conn.execute(text("UPDATE stock_counts SET counted_at='2026-10-22T16:30:00Z'"))
        purchase_history=self.client.get('/api/inventory/history?date_from=2026-05-07&date_to=2026-05-07&kind=purchase&item_id=powder')
        self.assertEqual(purchase_history.status_code,200,purchase_history.text)
        self.assertEqual(len(purchase_history.json()['updates']),1)
        self.assertEqual(purchase_history.json()['updates'][0]['value'],'100.000000')
        count_history=self.client.get('/api/inventory/history?date_from=2026-10-23&date_to=2026-10-23&kind=count')
        self.assertEqual(count_history.status_code,200,count_history.text)
        self.assertEqual({r['kind'] for r in count_history.json()['updates']},{'count','waste'})
        self.assertEqual(len(count_history.json()['updates']),2) # Parent batch is not counted twice.
        threshold_history=self.client.get('/api/inventory/history?date_from=2026-10-23&date_to=2026-10-23&kind=threshold')
        self.assertEqual(threshold_history.status_code,200,threshold_history.text)
        self.assertEqual(len(threshold_history.json()['updates']),2)
        self.assertEqual({r['value'] for r in threshold_history.json()['updates']},{'100.000000',None})
        self.assertEqual(threshold_history.json()['movements'],[])
        self.assertEqual(self.client.get('/api/inventory/history?date_from=2026-10-24&date_to=2026-10-23').status_code,400)
        self.assertEqual(self.client.get('/api/inventory/history?kind=invalid').status_code,422)


if __name__ == '__main__':
    unittest.main()
