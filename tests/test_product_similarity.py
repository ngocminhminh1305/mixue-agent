"""OCR-name regressions with fictional quantities and no provider requests."""
import unittest

from sales import matching_index, resolve_product


class SimilarityTests(unittest.TestCase):
    def setUp(self):
        self.products = [
            {'id':'m','name':'Trà Sữa Trân Châu','size':'M'},
            {'id':'l','name':'Trà Sữa Trân Châu (Size L)','size':'L'},
            {'id':'king','name':'Trà Sữa Bá Vương','size':'L'},
            {'id':'mango','name':'Dương Chi Cam Lộ','size':'M'},
            {'id':'lucky','name':'Lucky Sundae O-coco','size':None},
            {'id':'super','name':'Super Sundae O-coco','size':None},
            {'id':'pure','name':'Super Sundae O-coco Matcha (Kem Matcha)','size':None},
            {'id':'mix','name':'Super Sundae O-coco Matcha (Kem Mix)','size':None},
            {'id':'cone','name':'Kem Ốc Quế Nguyên Vị','size':None},
            {'id':'peach','name':'Trà Đào Bigsize','size':'L'},
            {'id':'strawberry','name':'Trà Đào Dâu Tây','size':'L'},
            {'id':'white','name':'Thêm Trân châu trắng','size':None,'category':'topping'},
            {'id':'original','name':'Thêm Trân châu nguyên vị','size':None,'category':'topping'},
        ]
        self.index=matching_index(self.products,[])
        self.available={p['id'] for p in self.products}

    def resolve(self,name,available=None,index=None):
        return resolve_product(name,self.index if index is None else index,self.available if available is None else available)

    def test_exact_rules_and_topping_aliases_still_win(self):
        self.assertEqual(self.resolve('Trân châu (MT)')['product_id'],'original')
        self.assertEqual(self.resolve('Trà Sữa Trân Châu (MT)')['product_id'],'m')
        self.assertEqual(self.resolve('Trà Sữa Trân Châu (Size L) (MT)')['product_id'],'l')
        self.assertEqual(self.resolve('Dương Chi Cam Lộ')['method'],'exact')

    def test_small_ocr_and_accent_errors_can_auto_match(self):
        for name,expected in [('Dương Chỉ Cam Lộ','mango'),('Dương Chii Cam Lộ','mango'),
                              ('Trà Sữa Ba Vương','king'),('Lucky Sundae Ô-coco','lucky')]:
            result=self.resolve(name)
            self.assertEqual(result['product_id'],expected,result)
            self.assertEqual(result['method'],'approximate')
            self.assertGreaterEqual(result['margin'],0.1)

    def test_fuzzy_matching_preserves_size_and_inactive_medium(self):
        self.assertEqual(self.resolve('Tra Sua Tran Chau (Size L)')['product_id'],'l')
        self.assertEqual(self.resolve('Tra Sua Tran Chau')['product_id'],'m')
        self.assertIsNone(self.resolve('Tra Sua Tran Chau',self.available-{'m'})['product_id'])
        self.assertIsNone(self.resolve('Tra Sua Tran Chau (Size L)',self.available-{'l'})['product_id'])
        self.assertIsNone(self.resolve('Tra Sua Tran Chau (Slze L)')['product_id'])

    def test_unspecified_ice_cream_flavour_requires_review(self):
        result=self.resolve('Super Sundae O-coco Matcha')
        self.assertIsNone(result['product_id'])
        self.assertEqual(result['method'],'review')
        self.assertEqual({r['product_id'] for r in result['suggestions']},{'pure','mix'})
        self.assertEqual(self.resolve('Super Sundae O-coco Matcha (Kem MlX)')['product_id'],None)

    def test_unavailable_flavours_and_unrelated_names_are_not_guessed(self):
        for name in ['Trà Xanh Kiwi','Kem Ốc Quế Khoai Môn','Super Sundae Khoai Môn',
                     'Sữa Tươi Trân Châu Khoai Môn','Trà Đen Bigsize','Random product xyz',None,'']:
            self.assertIsNone(self.resolve(name)['product_id'],name)

    def test_close_alternatives_get_ranked_suggestions_without_auto_selection(self):
        result=self.resolve('Trà Đào Bigzise')
        self.assertIsNone(result['product_id'])
        self.assertEqual(result['suggestions'][0]['product_id'],'peach')
        self.assertLessEqual(len(result['suggestions']),3)
        self.assertEqual(result['suggestions'],sorted(result['suggestions'],key=lambda r:-r['score']))

    def test_inactive_nearest_candidate_and_exact_ambiguity_block_auto_selection(self):
        self.assertIsNone(self.resolve('Trà Sữa Ba Vương',self.available-{'king'})['product_id'])
        index=matching_index(self.products,[{'name':'Drink','product_id':'m'},{'name':'Drink','product_id':'lucky'}])
        self.assertIsNone(self.resolve('Drink',index=index)['product_id'])

    def test_catalog_edits_refresh_model_without_mutating_names(self):
        original=[dict(p) for p in self.products]
        before=self.resolve('Dương Chỉ Cam Lộ')
        changed=[p for p in self.products if p['id']!='mango']
        index=matching_index(changed,[])
        after=self.resolve('Dương Chỉ Cam Lộ',index=index)
        self.assertEqual(before['product_id'],'mango')
        self.assertIsNone(after['product_id'])
        self.assertEqual(self.products,original)


if __name__=='__main__':
    unittest.main()
