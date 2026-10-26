import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
import shop_config


def sample():
    return {
        'revision': 0,
        'stock_items': [
            {'id': 'powder', 'name': 'Powder', 'unit': 'g', 'kind': 'raw'},
            {'id': 'base', 'name': 'Tea base', 'unit': 'ml', 'kind': 'prepared'},
        ],
        'products': [{'id': 'drink', 'name': 'Tea M', 'category': 'milk_tea', 'size': 'M', 'price_vnd': 25000,
                      'components': [{'item_id': 'base', 'quantity': 160}]}],
        'preparations': [{'id': 'prep', 'name': 'Prepare tea', 'output_item_id': 'base', 'output_quantity': 2000,
                          'inputs': [{'item_id': 'powder', 'quantity': 500}]}],
        'questions': ['Confirm yield'],
    }


class ShopConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch('shop_config.STORE', Path(self.temp.name) / 'shop_config.json')
        self.patch.start()
        self.backend_patch = patch('shop_config.storage_backend', return_value='json')
        self.backend_patch.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        self.patch.stop()
        self.backend_patch.stop()
        self.temp.cleanup()

    def test_save_reload_and_history(self):
        response = self.client.put('/api/shop-config', json=sample())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['revision'], 1)
        data['products'][0]['price_vnd'] = 27000
        self.assertEqual(self.client.put('/api/shop-config', json=data).status_code, 200)
        self.assertEqual(self.client.get('/api/shop-config').json()['products'][0]['price_vnd'], 27000)
        self.assertTrue((Path(self.temp.name) / 'shop_history/revision-1.json').exists())

    def test_concurrent_edit_is_rejected(self):
        self.client.put('/api/shop-config', json=sample())
        self.assertEqual(self.client.put('/api/shop-config', json=sample()).status_code, 409)

    def test_referenced_ingredient_cannot_be_removed(self):
        data = sample()
        data['stock_items'] = data['stock_items'][1:]
        self.assertEqual(self.client.put('/api/shop-config', json=data).status_code, 400)

    def test_negative_quantity_and_fractional_price_rejected(self):
        data = sample()
        data['products'][0]['components'][0]['quantity'] = -1
        self.assertEqual(self.client.put('/api/shop-config', json=data).status_code, 422)
        data = sample()
        data['products'][0]['price_vnd'] = 25000.5
        self.assertEqual(self.client.put('/api/shop-config', json=data).status_code, 422)

    def test_coffee_cannot_be_large(self):
        data = sample()
        data['products'][0].update(category='coffee', size='L')
        self.assertEqual(self.client.put('/api/shop-config', json=data).status_code, 400)

    def test_preparation_cannot_consume_itself(self):
        data = sample()
        data['preparations'][0]['inputs'][0]['item_id'] = 'base'
        self.assertEqual(self.client.put('/api/shop-config', json=data).status_code, 400)


if __name__ == '__main__':
    unittest.main()
