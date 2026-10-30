"""Validate confirmation and retry identifiers before any database write."""
import unittest
from uuid import uuid4

from pydantic import ValidationError

from bobaops.sales.reversals import ReverseReport


class ReversalValidationTests(unittest.TestCase):
    def sample(self):
        return {'request_id': str(uuid4()), 'revision': 2, 'preview_token': 'a' * 64,
                'reason': 'Wrong approved quantity', 'confirm_stock_impact': True}

    def test_confirmation_is_explicit_and_strict(self):
        body = ReverseReport.model_validate({k: v for k, v in self.sample().items() if k != 'confirm_stock_impact'})
        self.assertFalse(body.confirm_stock_impact)
        for value in ('yes', 'true', 1):
            with self.assertRaises(ValidationError):
                ReverseReport.model_validate({**self.sample(), 'confirm_stock_impact': value})

    def test_reversal_requires_valid_retry_revision_and_preview(self):
        for changes in ({'request_id': 'bad'}, {'revision': -1}, {'revision': True},
                        {'preview_token': 'bad'}, {'reason': ''}, {'reason': 'x' * 2001},
                        {'unexpected': 'value'}):
            with self.assertRaises(ValidationError):
                ReverseReport.model_validate({**self.sample(), **changes})
