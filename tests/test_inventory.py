import copy
import unittest
from decimal import Decimal

from inventory import calculate_consumption


class ConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'id': 'sale', 'raw_item_name': 'Tea', 'product_id': 'drink', 'quantity': 10}]
        self.products = {'drink': {'id': 'drink', 'name': 'Tea M', 'active': True, 'assumed': True}}
        self.stocks = {
            'powder': {'id': 'powder', 'name': 'Powder', 'unit': 'g', 'kind': 'raw', 'opening_stock': 1000},
            'tea': {'id': 'tea', 'name': 'Prepared tea', 'unit': 'ml', 'kind': 'prepared'},
            'cup': {'id': 'cup', 'name': 'Cup M', 'unit': 'piece', 'kind': 'packaging', 'opening_stock': 50},
        }
        self.recipes = {'drink': {'id': 'recipe', 'version': 1, 'assumed': True}}
        self.components = {'recipe': [{'item_id': 'tea', 'quantity': 160}, {'item_id': 'cup', 'quantity': 1}]}
        self.preparations = {'tea': {'id': 'prep-version', 'output_quantity': 2000, 'assumed': True}}
        self.inputs = {'prep-version': [{'item_id': 'powder', 'quantity': 500}]}
        self.prices = {'drink': {'id': 'price', 'price_vnd': 25000, 'assumed': True}}
        self.balances = {'powder': 1000, 'cup': 50}

    def calculate(self):
        return calculate_consumption(self.rows, self.products, self.stocks, self.recipes, self.components,
                                     self.preparations, self.inputs, self.prices, self.balances)

    def test_prepared_base_and_packaging_use_correct_raw_units(self):
        result = self.calculate()
        self.assertEqual(result['blockers'], [])
        used = {r['item_id']: Decimal(r['quantity']) for r in result['consumption']}
        self.assertEqual(used, {'powder': Decimal(400), 'cup': Decimal(10)})
        self.assertEqual(result['rows'][0]['preparation_version_ids'], ['prep-version'])
        powder = next(r for r in result['consumption'] if r['item_id'] == 'powder')
        self.assertEqual(Decimal(powder['balance_after']), 600)
        self.assertTrue(result['assumptions'])

    def test_ready_to_serve_topping_and_boba_yield_do_not_double_count(self):
        self.stocks['dry-boba'] = {'id': 'dry-boba', 'name': 'Dry boba', 'unit': 'g', 'kind': 'raw'}
        self.stocks['served-boba'] = {'id': 'served-boba', 'name': 'Served boba', 'unit': 'g', 'kind': 'prepared'}
        self.stocks['jelly'] = {'id': 'jelly', 'name': 'Ready jelly', 'unit': 'g', 'kind': 'raw'}
        self.components['recipe'] += [{'item_id': 'served-boba', 'quantity': 80}, {'item_id': 'jelly', 'quantity': 40}]
        self.preparations['served-boba'] = {'id': 'boba-version', 'output_quantity': 2000}
        self.inputs['boba-version'] = [{'item_id': 'dry-boba', 'quantity': 1000}]
        used = {r['item_id']: Decimal(r['quantity']) for r in self.calculate()['consumption']}
        self.assertEqual(used['dry-boba'], 400)
        self.assertEqual(used['jelly'], 400)
        self.assertNotIn('served-boba', used)
        self.assertNotIn('tea', used)

    def test_unmatched_and_duplicate_rows_block_approval(self):
        self.rows.append(copy.deepcopy(self.rows[0]))
        self.rows[-1]['id'] = 'other'
        self.assertTrue(any('duplicate' in x for x in self.calculate()['blockers']))
        self.rows[1]['product_id'] = None
        self.assertTrue(any('catalog product' in x for x in self.calculate()['blockers']))

    def test_missing_yield_or_empty_recipe_cannot_silently_skip_usage(self):
        self.preparations['tea']['output_quantity'] = None
        result = self.calculate()
        self.assertTrue(result['blockers'])
        self.assertEqual(result['consumption'], [])
        self.components['recipe'] = []
        self.assertTrue(any('recipe' in x for x in self.calculate()['blockers']))

    def test_unknown_opening_stock_is_not_reported_as_known_zero(self):
        self.stocks['powder']['opening_stock'] = None
        self.balances['powder'] = 0
        result = self.calculate()
        powder = next(r for r in result['consumption'] if r['item_id'] == 'powder')
        self.assertIsNone(powder['balance_before'])
        self.assertIsNone(powder['balance_after'])
        self.assertEqual(Decimal(powder['ledger_balance_after']), -400)
        self.assertTrue(any('starting stock is unknown' in x for x in result['warnings']))

    def test_fractional_packaging_is_blocked(self):
        self.components['recipe'][1]['quantity'] = Decimal('0.15')
        self.assertTrue(any('whole pieces' in x for x in self.calculate()['blockers']))

    def test_zero_sales_create_no_movements_and_unknown_price_is_a_warning(self):
        self.rows[0]['quantity'] = 0
        self.prices['drink']['price_vnd'] = None
        result = self.calculate()
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['consumption'], [])
        self.assertEqual(result['rows'][0]['deductions'], [])
        self.assertTrue(any('price is unknown' in x for x in result['warnings']))

    def test_rounding_matches_the_sum_of_per_row_movements(self):
        self.components['recipe'] = [{'item_id': 'tea', 'quantity': 1}]
        self.rows[0]['quantity'] = 1
        self.preparations['tea']['output_quantity'] = 3
        self.inputs['prep-version'][0]['quantity'] = 1
        result = self.calculate()
        self.assertEqual(Decimal(result['rows'][0]['deductions'][0]['quantity']), Decimal('0.333333'))
        self.assertEqual(result['rows'][0]['deductions'][0]['quantity'], result['consumption'][0]['quantity'])


    def test_manual_ice_cream_skips_powders_but_keeps_packaging(self):
        self.stocks['tea']['unit'] = 'g'
        self.stocks['powder']['ice_cream_powder'] = True
        self.preparations['tea']['deduction_mode'] = 'manual_ice_cream'
        self.preparations['tea']['output_quantity'] = None
        result = self.calculate()
        self.assertEqual(result['blockers'], [])
        self.assertEqual([r['item_id'] for r in result['consumption']], ['cup'])
        self.assertEqual(result['manual_ice_cream'][0]['item_id'], 'tea')
        self.assertIn('prep-version', result['rows'][0]['preparation_version_ids'])

    def test_shared_powder_in_automatic_drink_base_is_still_deducted(self):
        self.stocks['tea']['unit'] = 'g'
        self.stocks['powder']['ice_cream_powder'] = True
        self.preparations['tea']['deduction_mode'] = 'manual_ice_cream'
        self.stocks['concentrate'] = {'id': 'concentrate', 'name': 'Drink concentrate', 'unit': 'ml', 'kind': 'prepared'}
        self.components['recipe'].append({'item_id': 'concentrate', 'quantity': 160})
        self.preparations['concentrate'] = {'id': 'drink-prep', 'output_quantity': 2000}
        self.inputs['drink-prep'] = [{'item_id': 'powder', 'quantity': 500}]
        result = self.calculate()
        self.assertEqual(result['blockers'], [])
        usage = {r['item_id']: Decimal(r['quantity']) for r in result['consumption']}
        self.assertEqual(usage, {'cup': Decimal(10), 'powder': Decimal(400)})


if __name__ == '__main__':
    unittest.main()
