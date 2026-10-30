ALTER TABLE stock_items ADD COLUMN ice_cream_powder boolean NOT NULL DEFAULT false;
ALTER TABLE stock_items ADD CONSTRAINT ice_cream_powder_unit CHECK (NOT ice_cream_powder OR (kind = 'raw' AND unit = 'g'));
ALTER TABLE preparation_versions ADD COLUMN deduction_mode varchar(30) NOT NULL DEFAULT 'automatic' CHECK (deduction_mode IN ('automatic', 'manual_ice_cream'));
ALTER TABLE inventory_operations DROP CONSTRAINT inventory_operations_kind_check;
ALTER TABLE inventory_operations ADD CONSTRAINT inventory_operations_kind_check CHECK (kind IN ('purchase', 'count', 'waste', 'threshold', 'ice_cream_usage'));
ALTER TABLE inventory_movements DROP CONSTRAINT inventory_movements_kind_check;
ALTER TABLE inventory_movements ADD CONSTRAINT inventory_movements_kind_check CHECK (kind IN ('opening', 'opening_adjustment', 'purchase', 'sale', 'waste', 'correction', 'reversal', 'ice_cream_usage'));
ALTER TABLE inventory_movements DROP CONSTRAINT inventory_movements_check1;
ALTER TABLE inventory_movements ADD CONSTRAINT inventory_movement_source CHECK (
    (kind = 'sale' AND sale_item_id IS NOT NULL AND signed_quantity < 0 AND purchase_item_id IS NULL AND reversal_of IS NULL) OR
    (kind = 'purchase' AND purchase_item_id IS NOT NULL AND signed_quantity > 0 AND sale_item_id IS NULL AND reversal_of IS NULL) OR
    (kind = 'waste' AND signed_quantity < 0 AND sale_item_id IS NULL AND purchase_item_id IS NULL AND reversal_of IS NULL) OR
    (kind = 'opening' AND signed_quantity > 0 AND sale_item_id IS NULL AND purchase_item_id IS NULL AND reversal_of IS NULL) OR
    (kind = 'reversal' AND reversal_of IS NOT NULL AND sale_item_id IS NULL AND purchase_item_id IS NULL) OR
    (kind IN ('opening_adjustment', 'correction', 'ice_cream_usage') AND sale_item_id IS NULL AND purchase_item_id IS NULL AND reversal_of IS NULL)
);
CREATE TABLE ice_cream_daily_usage (
    used_on date NOT NULL,
    item_id varchar(100) NOT NULL REFERENCES stock_items(id),
    quantity numeric(20,6) NOT NULL CHECK (quantity >= 0),
    operation_id varchar(100) NOT NULL REFERENCES inventory_operations(id),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (used_on, item_id)
);
CREATE INDEX ix_ice_cream_usage_item ON ice_cream_daily_usage(item_id, used_on);
