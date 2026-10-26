ALTER TABLE stock_items ADD COLUMN low_stock_threshold numeric(20,6) CHECK (low_stock_threshold >= 0);
ALTER TABLE stock_items ADD CONSTRAINT whole_piece_threshold CHECK (unit <> 'piece' OR low_stock_threshold = trunc(low_stock_threshold));
ALTER TABLE purchases ADD COLUMN purchased_on date;

CREATE TABLE inventory_operations (
    id varchar(100) PRIMARY KEY,
    kind varchar(20) NOT NULL CHECK (kind IN ('purchase', 'count', 'waste', 'threshold')),
    request_hash char(64) NOT NULL,
    payload jsonb NOT NULL,
    result jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE stock_counts (
    id varchar(100) PRIMARY KEY REFERENCES inventory_operations(id),
    item_id varchar(100) NOT NULL REFERENCES stock_items(id),
    counted_quantity numeric(20,6) NOT NULL CHECK (counted_quantity >= 0),
    ledger_balance_before numeric(20,6) NOT NULL,
    adjustment_quantity numeric(20,6) NOT NULL,
    movement_id varchar(150) UNIQUE REFERENCES inventory_movements(id),
    counted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_stock_count_item ON stock_counts (item_id, counted_at);

CREATE OR REPLACE VIEW inventory_balances AS
SELECT s.id AS item_id, s.name, s.unit, COALESCE(sum(m.signed_quantity), 0) AS quantity,
       (COALESCE(sum(m.signed_quantity), 0) < 0) AS negative_stock,
       (s.opening_stock IS NOT NULL OR EXISTS (SELECT 1 FROM stock_counts c WHERE c.item_id = s.id)) AS opening_stock_known
FROM stock_items s LEFT JOIN inventory_movements m ON m.item_id = s.id
WHERE s.kind <> 'prepared' AND NOT s.is_deleted
GROUP BY s.id, s.name, s.unit, s.opening_stock;
