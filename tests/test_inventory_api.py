import unittest
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from bobaops.inventory.routes import CountBatch, Purchase, StockCount, Threshold, ThresholdBatch, Waste, validate_quantity


class InventoryEntryValidationTests(unittest.TestCase):
    def request(self):
        return {'request_id': str(uuid4()), 'item_id': 'powder', 'balance_token': 'a' * 64}

    def test_count_allows_zero_and_requires_reason(self):
        data = {**self.request(), 'actual_quantity': '0', 'reason': '  Weekly count  '}
        self.assertEqual(StockCount.model_validate(data).actual_quantity, 0)
        self.assertEqual(StockCount.model_validate(data).reason, 'Weekly count')
        with self.assertRaises(ValidationError):
            StockCount.model_validate({**data, 'reason': '   '})

    def test_invalid_quantities_and_precision_are_rejected(self):
        for quantity in ('-1', 'NaN', 'Infinity', '0.0000001', '100000000000000'):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):
                StockCount.model_validate({**self.request(), 'actual_quantity': quantity, 'reason': 'Count'})
        with self.assertRaises(ValidationError):
            Waste.model_validate({**self.request(), 'quantity': '0', 'reason': 'Spill'})

    def test_purchase_rejects_duplicate_items_and_fractional_cost(self):
        data = {'request_id': str(uuid4()), 'purchased_on': '2026-10-22',
                'items': [{'item_id': 'powder', 'quantity': '1000', 'total_cost_vnd': 50000}]}
        self.assertEqual(Purchase.model_validate(data).items[0].quantity, 1000)
        with self.assertRaises(ValidationError):
            Purchase.model_validate({**data, 'items': data['items'] * 2})
        data['items'][0]['total_cost_vnd'] = 1.5
        with self.assertRaises(ValidationError):
            Purchase.model_validate(data)

    def test_whole_piece_and_optional_threshold_rules(self):
        validate_quantity(Decimal(2), 'piece')
        validate_quantity(Decimal('0.5'), 'g')
        with self.assertRaises(HTTPException):
            validate_quantity(Decimal('0.5'), 'piece')
        self.assertIsNone(Threshold.model_validate({**self.request(), 'threshold': None}).threshold)

    def test_table_batches_allow_zero_counts_and_reject_duplicate_rows(self):
        row = {'item_id': 'powder', 'balance_token': 'a' * 64, 'actual_quantity': '0', 'reason': 'Physical count'}
        self.assertEqual(CountBatch.model_validate({'request_id': str(uuid4()), 'items': [row]}).items[0].actual_quantity, 0)
        with self.assertRaises(ValidationError):
            CountBatch.model_validate({'request_id': str(uuid4()), 'items': [row, row]})
        with self.assertRaises(ValidationError):
            CountBatch.model_validate({'request_id': str(uuid4()), 'items': []})
        level = ThresholdBatch.model_validate({'request_id': str(uuid4()), 'items': [
            {'item_id': 'powder', 'balance_token': 'a' * 64, 'threshold': None}]})
        self.assertIsNone(level.items[0].threshold)


if __name__ == '__main__':
    unittest.main()
