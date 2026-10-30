"""Synthetic fixtures only; private shop data is never required by tests."""
import copy
import unittest

from scripts.import_confirmed_catalog import compile_catalog
from bobaops.sales.routes import match_product, matching_index
from bobaops.catalog.schemas import ShopConfig, check_config


def fixture():
    raw=[('Bột kem nguyên vị','g'),('Bột matcha','g'),('Bột trà sữa','g'),('Bột cà phê rang','g'),
         ('Đường mật','ml'),('Vụn oreo','g'),('Thạch dừa','g')]
    packs=[(n,'cái') for n in ['Cốc M 500 ml','Cốc L 700 ml','Cốc U 400 ml','Ốc quế','Ống hút lớn','Ống hút nhỏ']]
    def recipe(name,cup,parts,size=None):
        return {'selling_name':name,'cup_ml':cup,'size':size,'selling_price_vnd':25000,
                'components':[{'name':n,'quantity':q,'unit':u} for n,q,u in parts]}
    def prep(name,unit,output,raw,quantity):
        return {'output_ingredient':name,'output_unit':unit,'standard_batch':{
            'usable_output_quantity_'+unit:output,'tracked_inputs':[{'name':raw,'quantity':quantity,'unit':'g'}]}}
    return {'demo':{'synthetic':True,'items':[{'name':n,'unit':u,'carton_quantity':10000 if u!='cái' else 1000,
              'carton_price_vnd':200000,'carton_description':'Synthetic carton','opening_stock':5000 if u!='cái' else 500,
              'low_stock_threshold':100} for n,u in raw+packs]},
        'preparations':[prep('Kem nguyên vị','g',5500,'Bột kem nguyên vị',1500),
                        prep('Kem matcha','g',5400,'Bột matcha',200),
                        prep('Trà sữa lạnh','ml',3050,'Bột trà sữa',650),
                        prep('Cà phê tươi','ml',3380,'Bột cà phê rang',380)],
        'recipes':[
            recipe('Trà Sữa Dừa',500,[('Trà sữa lạnh',160,'ml'),('Thạch dừa',50,'g')],'M'),
            recipe('Trà Sữa Dừa (Size L)',700,[('Trà sữa lạnh',200,'ml'),('Thạch dừa',150,'g')],'L'),
            recipe('Cafe Latte',400,[('Cà phê tươi',120,'ml')]),
            recipe('Super Sundae Oreo',400,[('Kem tươi',180,'g'),('Vụn oreo',16,'g')]),
            recipe('Kem Ốc Quế Nguyên Vị',None,[('Kem',None,'g'),('Ốc quế',1,'piece')])],
        'toppings':[{'name':'Thạch dừa','price_vnd':3000,'portion_quantity':25}]}


def empty():
    return ShopConfig(revision=0,stock_items=[],products=[],preparations=[],questions=[])


class CatalogCompilerTests(unittest.TestCase):
    def test_matching_sizes_packaging_and_carton_costs(self):
        plan=compile_catalog(fixture(),empty());c=plan['config']
        byname={x.name:x for x in c.products};stock={x.id:x for x in c.stock_items}
        self.assertEqual(byname['Super Sundae Oreo'].category,'sundae')
        self.assertIsNone(byname['Super Sundae Oreo'].size)
        coffee=byname['Cafe Latte']
        self.assertTrue(any(stock[x.item_id].name=='Cốc U 400 ml' for x in coffee.components))
        self.assertTrue(any(stock[x.item_id].name=='Ống hút nhỏ' for x in coffee.components))
        self.assertTrue(any(stock[x.item_id].name=='Ống hút lớn' for x in byname['Trà Sữa Dừa'].components))
        i=matching_index([x.model_dump() for x in c.products],[])
        self.assertEqual(match_product('Trà Sữa Dừa (MT)',i),byname['Trà Sữa Dừa'].id)
        self.assertEqual(match_product('Trà Sữa Dừa (Size L) (MT)',i),byname['Trà Sữa Dừa (Size L)'].id)
        self.assertEqual(byname['Thêm Thạch dừa'].price_vnd,3000)
        self.assertEqual(len(byname['Thêm Thạch dừa'].components),1)
        self.assertTrue(all(x.purchase_unit=='thùng' for x in c.stock_items if x.kind!='prepared'))
        self.assertEqual(next(x.cost_per_unit for x in stock.values() if x.name=='Bột trà sữa'),20)

    def test_unknown_cone_mass_is_allowed_only_for_manual_ice_cream(self):
        c=compile_catalog(fixture(),empty())['config']
        cone=next(p for p in c.products if p.category=='cone')
        self.assertTrue(any(x.quantity is None for x in cone.components))
        check_config(c)
        cone.components[-1].quantity=None
        with self.assertRaisesRegex(ValueError,'quantity is required'):
            check_config(c)

    def test_unit_or_kind_change_creates_new_identity_and_import_ids_are_stable(self):
        sources=fixture();first=compile_catalog(sources,empty())
        old=first['config'];sugar=next(x for x in old.stock_items if x.name=='Đường mật')
        old_id=sugar.id;sugar.unit='g';sugar.id='legacy_sugar'
        second=compile_catalog(sources,old)
        self.assertEqual(first['import_id'],second['import_id'])
        current=next(x for x in second['config'].stock_items if x.name=='Đường mật')
        self.assertNotEqual(current.id,'legacy_sugar')
        self.assertEqual(current.id,old_id)
        incomplete=copy.deepcopy(sources);incomplete['demo']['items'].pop(3)
        with self.assertRaisesRegex(ValueError,'does not cover'):
            compile_catalog(incomplete,empty())


if __name__=='__main__':
    unittest.main()
