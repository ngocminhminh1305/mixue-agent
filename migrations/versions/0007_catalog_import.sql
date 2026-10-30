ALTER TABLE products DROP CONSTRAINT products_category_check;
ALTER TABLE products DROP CONSTRAINT products_check;
ALTER TABLE products ADD CONSTRAINT products_category_check CHECK (category IN ('fruit_tea', 'milk_tea', 'coffee', 'cone', 'topping', 'sundae'));
ALTER TABLE products ADD CONSTRAINT products_category_size CHECK (
 (category = 'coffee' AND size = 'M' AND size IS NOT NULL) OR
 (category IN ('fruit_tea', 'milk_tea') AND size IS NOT NULL) OR
 (category IN ('cone', 'topping', 'sundae') AND size IS NULL));
ALTER TABLE stock_items ADD COLUMN purchase_unit varchar(30);
ALTER TABLE stock_items ADD COLUMN purchase_quantity numeric(20,6) CHECK (purchase_quantity > 0);
ALTER TABLE stock_items ADD COLUMN purchase_price_vnd bigint CHECK (purchase_price_vnd >= 0);
ALTER TABLE stock_items ADD COLUMN purchase_description text NOT NULL DEFAULT '';
ALTER TABLE stock_items ADD CONSTRAINT whole_piece_purchase CHECK (unit <> 'piece' OR purchase_quantity = trunc(purchase_quantity));
ALTER TABLE recipe_components ALTER COLUMN quantity DROP NOT NULL;
CREATE TABLE catalog_imports (
 id char(64) PRIMARY KEY,
 imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 config_revision integer NOT NULL REFERENCES config_history(revision),
 summary jsonb NOT NULL,
 synthetic_inventory boolean NOT NULL
);
