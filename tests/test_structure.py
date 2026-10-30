"""Package entry points and resource paths must survive folder reorganization."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

from auth_support import authenticated_client

import app
from bobaops.core.paths import FRONTEND_DIR, PRIVATE_DATA_DIR, ROOT
from bobaops.extraction import routes as extraction
from bobaops.main import app as packaged_app


class StructureTests(unittest.TestCase):
    def test_existing_uvicorn_entry_point_and_frontend_assets(self):
        self.assertIs(app.app, packaged_app)
        client = authenticated_client(self, packaged_app)
        for path in ('/', '/dashboard', '/upload', '/sales', '/inventory', '/setup', '/chat',
                     '/assets/app.css', '/assets/ui.js', '/assets/receipt-powders.js'):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertIn('<title>Tổng quan', client.get('/').text)

    def test_resources_and_private_data_remain_at_their_intended_paths(self):
        self.assertEqual(ROOT, Path(__file__).resolve().parents[1])
        self.assertEqual(PRIVATE_DATA_DIR, ROOT / 'data/private')
        self.assertEqual(FRONTEND_DIR, ROOT / 'frontend')
        self.assertEqual(extraction.SCHEMA['type'], 'object')
        self.assertTrue(extraction.PROMPT.strip())
        from bobaops.catalog import routes as catalog
        from bobaops.sales import routes as sales
        self.assertEqual(catalog.STORE, PRIVATE_DATA_DIR / 'shop_config.json')
        self.assertEqual(sales.IMAGE_DIR, PRIVATE_DATA_DIR / 'sales_images')

    def test_import_and_static_pages_do_not_depend_on_shell_working_directory(self):
        code = """
from fastapi.testclient import TestClient
from bobaops.main import app
from bobaops.extraction.routes import PROMPT
from scripts import import_shop_config, import_confirmed_catalog
assert PROMPT.strip()
assert callable(import_shop_config.main)
assert callable(import_confirmed_catalog.main)
assert TestClient(app).get('/login').status_code == 200
assert TestClient(app).get('/upload', follow_redirects=False).status_code == 303
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', code], cwd=directory,
                                    env={**os.environ, 'PYTHONPATH': str(ROOT)},
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
