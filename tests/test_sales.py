import unittest

from pydantic import ValidationError

from bobaops.sales.routes import DraftEdit, Extraction, match_product, matching_index, name_and_size, normalized_name, row_warnings


class SalesValidationTests(unittest.TestCase):
    def test_topping_receipt_names_without_them_and_short_original_boba(self):
        names = ['Trân châu trắng', 'Thạch dừa', 'Lô hội', 'Trân châu nguyên vị',
                 'Thạch đào', 'Thạch đường đen', 'Vụn ốc quế', 'Vụn oreo']
        products = [{'id': str(i), 'name': 'Thêm '+name, 'category': 'topping', 'size': None}
                    for i, name in enumerate(names)]
        index = matching_index(products, [])
        for i, name in enumerate(names):
            self.assertEqual(match_product(name.upper()+' (MT)', index), str(i))
            self.assertEqual(match_product('Thêm '+name, index), str(i))
        self.assertEqual(match_product(' trân châu (MT) ', index), '3')
        self.assertIsNone(match_product('Trân châu đen', index))
        self.assertIsNone(match_product('Thạch', index))
        self.assertIsNone(match_product('Trân châu', index, available={'0'}))

    def test_topping_aliases_preserve_ambiguity_and_drink_names(self):
        products = [
            {'id': 't', 'name': 'Thêm Trân châu nguyên vị', 'category': 'topping'},
            {'id': 'other', 'name': 'Trân châu', 'category': 'topping'},
            {'id': 'drink', 'name': 'Thêm Trà Sữa', 'category': 'milk_tea', 'size': 'M'}]
        index = matching_index(products, [])
        self.assertIsNone(match_product('Trân châu', index))
        self.assertIsNone(match_product('Trà Sữa', index))
        self.assertEqual(match_product('Thêm Trà Sữa', index), 'drink')

    def test_match_pos_suffix_but_preserve_accents_and_sizes(self):
        self.assertEqual(normalized_name('  Trà Sữa  M (MT) '), normalized_name('Trà sữa M'))
        self.assertNotEqual(normalized_name('Trà sữa M'), normalized_name('Trà sữa L'))
        self.assertNotEqual(normalized_name('Trà sữa'), normalized_name('Tra sua'))

    def test_ambiguous_aliases_are_not_automatically_resolved(self):
        products = [{'id': 'first', 'name': 'Green tea M'}, {'id': 'second', 'name': 'Black tea M'}]
        aliases = [{'product_id': 'first', 'name': 'Tea'}, {'product_id': 'second', 'name': 'Tea'},
                   {'product_id': 'deleted', 'name': 'Other'}]
        matches = matching_index(products, aliases)
        self.assertEqual(matches[('tea', None)], {'first', 'second'})
        self.assertIsNone(match_product('Tea', matches))
        self.assertIsNone(match_product('Other', matches))

    def test_unique_size_matches_without_size_in_receipt(self):
        index = matching_index([{'id': 'lemon', 'name': 'Nước chanh tươi lạnh (size L)', 'size': 'L'}], [])
        self.assertEqual(match_product(' Nước Chanh Tươi Lạnh (MT) ', index), 'lemon')
        self.assertIsNone(match_product('Nước Chanh Tươi Lạnh (Size M) (MT)', index))

    def test_explicit_large_size_selects_correct_variant(self):
        index = matching_index([
            {'id': 'm', 'name': 'Trà sữa thạch đường đen (size M)', 'size': 'M'},
            {'id': 'l', 'name': 'Trà sữa thạch đường đen (size L)', 'size': 'L'},
        ], [])
        self.assertEqual(match_product('Trà Sữa Thạch Đường Đen (Size L) (MT)', index), 'l')
        self.assertEqual(match_product('Trà Sữa Thạch Đường Đen (Size M) (MT)', index), 'm')
        self.assertEqual(match_product('Trà Sữa Thạch Đường Đen (MT)', index), 'm')

    def test_default_medium_does_not_hide_unrelated_alias_ambiguity(self):
        index = matching_index([
            {'id': 'm', 'name': 'Tea (size M)', 'size': 'M'},
            {'id': 'l', 'name': 'Tea (size L)', 'size': 'L'},
            {'id': 'other', 'name': 'Other drink (size M)', 'size': 'M'},
        ], [{'product_id': 'other', 'name': 'Tea'}])
        self.assertIsNone(match_product('Tea', index))

    def test_missing_catalog_product_remains_unmatched(self):
        index = matching_index([{'id': 'tea', 'name': 'Tea (size M)', 'size': 'M'}], [])
        self.assertIsNone(match_product('Unlisted drink (MT)', index))

    def test_size_formats_case_unicode_and_whitespace(self):
        import unicodedata
        expected = ('trà sữa', 'L')
        for label in ('Trà Sữa (Size L) (MT)', 'Trà Sữa ( L )', 'Trà Sữa size L', 'Trà Sữa L'):
            self.assertEqual(name_and_size(label), expected)
        self.assertEqual(name_and_size(unicodedata.normalize('NFD', 'TRÀ SỮA (size l)')), expected)
        self.assertEqual(normalized_name('Trà ( mt ) sữa'), 'trà sữa')

    def test_bigsize_and_non_size_variant_text_are_preserved(self):
        self.assertEqual(name_and_size('Trà đào Bigsize (MT)'), ('trà đào bigsize', None))
        index = matching_index([{'id': 'peach', 'name': 'Trà đào bigsize (size L)', 'size': 'L'}], [])
        self.assertEqual(match_product('Trà Đào Bigsize (MT)', index), 'peach')
        self.assertIsNone(match_product('Trà đào (MT)', index))

    def test_conflicting_alias_size_cannot_override_catalog_size(self):
        index = matching_index([{'id': 'large', 'name': 'Tea (size L)', 'size': 'L'}],
                               [{'product_id': 'large', 'name': 'Other (size M)'}])
        self.assertIsNone(match_product('Other (size M)', index))

    def test_inactive_variant_still_prevents_size_guessing(self):
        index = matching_index([{'id': 'm', 'name': 'Tea (size M)', 'size': 'M'},
                                {'id': 'l', 'name': 'Tea (size L)', 'size': 'L'}], [])
        self.assertIsNone(match_product('Tea', index, available={'l'}))
        self.assertIsNone(match_product('Tea (size M)', index, available={'l'}))
        self.assertEqual(match_product('Tea (size L)', index, available={'l'}), 'l')

    def test_overlap_missing_quantity_and_unknown_product_need_review(self):
        rows = [
            {'id': 'a', 'raw_item_name': 'Tea (MT)', 'quantity': 5, 'product_id': 'tea'},
            {'id': 'b', 'raw_item_name': 'Tea', 'quantity': 5, 'product_id': 'tea'},
            {'id': 'c', 'raw_item_name': '', 'quantity': None, 'product_id': None},
        ]
        warnings = row_warnings(rows, {'tea'})
        self.assertTrue(any('overlap' in issue for issue in warnings['a']))
        self.assertTrue(any('overlap' in issue for issue in warnings['b']))
        self.assertEqual(len(warnings['c']), 3)

    def test_incomplete_extraction_can_be_saved_as_draft(self):
        result = Extraction.model_validate({'date': None, 'items': [{'item_name': None, 'quantity': None, 'issues': []}], 'issues': []})
        self.assertIsNone(result.date)
        self.assertIsNone(result.items[0].quantity)

    def test_edit_rejects_fractional_negative_and_overflow_quantities(self):
        for value in (1.5, -1, True, 2147483648):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                DraftEdit.model_validate({'revision': 1, 'date': '2026-10-22',
                                          'items': [{'raw_item_name': 'Tea', 'quantity': value}]})


if __name__ == '__main__':
    unittest.main()
