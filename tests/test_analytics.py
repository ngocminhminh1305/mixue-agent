"""Pure aggregation checks; no database or provider calls."""
import unittest
from datetime import date

from analytics import summarize_sales


class AnalyticsTests(unittest.TestCase):
    def report(self, identifier='r1', day=1, status='approved', snapshots=None):
        return {'id': identifier, 'report_date': date(2026, 10, day), 'status': status,
                'approval_snapshot': {'rows': snapshots or []}}

    def sale(self, identifier='s1', report='r1', quantity=3, price=25000, category='milk_tea', assumed=True):
        return {'id': identifier, 'report_id': report, 'product_id': identifier, 'raw_item_name': 'Receipt name',
                'quantity': quantity, 'unit_price_vnd': price, 'current_category': category,
                'current_name': 'Current name', 'price_assumed': assumed}

    def summarize(self, reports, sales, end=2):
        return summarize_sales(reports, sales, date(2026, 10, 1), date(2026, 10, end))

    def test_only_approved_sales_and_missing_dates_are_gaps(self):
        result = self.summarize([self.report(), self.report('r2', 2, 'draft')],
                                [self.sale(), self.sale('s2', 'r2', 99)])
        self.assertEqual(result['summary']['drinks'], 3)
        self.assertEqual(result['summary']['revenue_vnd'], 75000)
        self.assertIsNone(result['daily'][1]['drinks'])
        self.assertIsNone(result['daily'][1]['revenue_vnd'])
        self.assertEqual(result['missing_dates'], ['2026-10-02'])

    def test_missing_prices_and_categories_are_separate(self):
        result = self.summarize([self.report()], [self.sale(assumed=False),
                 self.sale('cone', quantity=2, price=None, category='cone'),
                 self.sale('topping', quantity=4, price=5000, category='topping')])['summary']
        self.assertEqual((result['drinks'], result['cones'], result['toppings']), (3, 2, 4))
        self.assertEqual(result['revenue_vnd'], 95000)
        self.assertEqual(result['confirmed_price_revenue_vnd'], 75000)
        self.assertEqual(result['assumed_price_revenue_vnd'], 20000)
        self.assertTrue(result['revenue_is_partial'])
        self.assertTrue(result['revenue_is_provisional'])

    def test_sundaes_are_counted_separately_and_keep_revenue(self):
        summary = self.summarize([self.report()], [self.sale(category='sundae',quantity=4,assumed=False)])['summary']
        self.assertEqual(summary['sundaes'],4)
        self.assertEqual(summary['drinks'],0)
        self.assertEqual(summary['uncategorized'],0)
        self.assertEqual(summary['revenue_vnd'],100000)

    def test_all_prices_unknown_is_not_zero_revenue(self):
        result = self.summarize([self.report()], [self.sale(price=None)])['summary']
        self.assertEqual(result['drinks'], 3)
        self.assertIsNone(result['revenue_vnd'])

    def test_frozen_name_category_and_price_provenance_win(self):
        snapshot = {'sale_item_id': 's1', 'product_name': 'Approved name',
                    'product_category': 'coffee', 'unit_price_assumed': False}
        result = self.summarize([self.report(snapshots=[snapshot])], [self.sale(category='cone')])
        self.assertEqual(result['products'][0]['name'], 'Approved name')
        self.assertEqual(result['products'][0]['category'], 'coffee')
        self.assertEqual(result['summary']['drinks'], 3)
        self.assertFalse(result['summary']['revenue_is_provisional'])
        self.assertEqual(result['summary']['legacy_classification_rows'], 0)

    def test_legacy_category_fallback_is_disclosed(self):
        result = self.summarize([self.report()], [self.sale()])
        self.assertEqual(result['summary']['legacy_classification_rows'], 1)
        self.assertTrue(any('current catalog' in warning for warning in result['warnings']))

    def test_empty_or_reversed_reports_are_not_zero_sales(self):
        for reports in ([], [self.report(status='reversed')], [self.report()]):
            result = self.summarize(reports, [])
            self.assertIsNone(result['summary']['drinks'])
            self.assertIsNone(result['summary']['revenue_vnd'])
            self.assertIsNone(result['daily'][0]['drinks'])

    def test_missing_quantity_is_not_zero(self):
        result = self.summarize([self.report()], [self.sale(quantity=None)])
        self.assertIsNone(result['summary']['drinks'])
        self.assertTrue(result['summary']['revenue_is_partial'])

    def test_recorded_zero_quantity_is_zero_sales(self):
        result = self.summarize([self.report()], [self.sale(quantity=0, price=None)])
        self.assertEqual(result['daily'][0]['drinks'], 0)
        self.assertEqual(result['summary']['revenue_vnd'], 0)
        self.assertFalse(result['summary']['revenue_is_partial'])

    def test_active_correction_wins_daily_coverage_in_either_row_order(self):
        reversed_report = self.report('old', 1, 'reversed')
        draft = self.report('new', 1, 'draft')
        for reports in ([draft, reversed_report], [reversed_report, draft]):
            data = self.summarize(reports, [self.sale(report='old')])
            self.assertEqual(data['daily'][0]['status'], 'draft')
            self.assertEqual(data['daily'][0]['report_id'], 'new')
            self.assertIsNone(data['summary']['drinks'])
