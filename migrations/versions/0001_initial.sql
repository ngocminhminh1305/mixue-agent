CREATE TABLE shop_state (
    id integer PRIMARY KEY CHECK (id = 1),
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0)
);
INSERT INTO shop_state (id, revision) VALUES (1, 0);

CREATE TABLE config_history (
    revision integer PRIMARY KEY,
    saved_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload jsonb NOT NULL
);

CREATE TABLE stock_items (
    id varchar(100) PRIMARY KEY,
    name varchar(200) NOT NULL CHECK (length(trim(name)) > 0),
    unit varchar(10) NOT NULL CHECK (unit IN ('g', 'ml', 'piece')),
    kind varchar(20) NOT NULL CHECK (kind IN ('raw', 'prepared', 'packaging')),
    opening_stock numeric(20,6) CHECK (opening_stock >= 0),
    cost_per_unit numeric(20,6) CHECK (cost_per_unit >= 0),
    assumed boolean NOT NULL DEFAULT true,
    notes text NOT NULL DEFAULT '',
    is_deleted boolean NOT NULL DEFAULT false,
    CHECK (unit <> 'piece' OR opening_stock = trunc(opening_stock))
);
CREATE UNIQUE INDEX uq_stock_name ON stock_items (lower(trim(name))) WHERE NOT is_deleted;

CREATE TABLE products (
    id varchar(100) PRIMARY KEY,
    name varchar(200) NOT NULL CHECK (length(trim(name)) > 0),
    category varchar(20) NOT NULL CHECK (category IN ('fruit_tea', 'milk_tea', 'coffee', 'cone', 'topping')),
    size varchar(1) CHECK (size IN ('M', 'L')),
    active boolean NOT NULL DEFAULT true,
    assumed boolean NOT NULL DEFAULT true,
    notes text NOT NULL DEFAULT '',
    is_deleted boolean NOT NULL DEFAULT false,
    CHECK ((category = 'coffee' AND size IS NOT NULL AND size = 'M') OR
           (category IN ('fruit_tea', 'milk_tea') AND size IS NOT NULL) OR
           (category IN ('cone', 'topping') AND size IS NULL))
);
CREATE UNIQUE INDEX uq_product_name ON products (lower(trim(name))) WHERE NOT is_deleted;

CREATE TABLE product_aliases (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id varchar(100) NOT NULL REFERENCES products(id),
    name varchar(200) NOT NULL CHECK (length(trim(name)) > 0)
);
CREATE UNIQUE INDEX uq_alias_name ON product_aliases (lower(trim(name)));

CREATE TABLE price_versions (
    id varchar(150) PRIMARY KEY,
    product_id varchar(100) NOT NULL REFERENCES products(id),
    version integer NOT NULL CHECK (version > 0),
    effective_from timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    price_vnd bigint CHECK (price_vnd >= 0),
    assumed boolean NOT NULL,
    UNIQUE (product_id, version),
    UNIQUE (id, product_id)
);

CREATE TABLE recipe_versions (
    id varchar(150) PRIMARY KEY,
    product_id varchar(100) NOT NULL REFERENCES products(id),
    version integer NOT NULL CHECK (version > 0),
    effective_from timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    assumed boolean NOT NULL,
    notes text NOT NULL DEFAULT '',
    UNIQUE (product_id, version),
    UNIQUE (id, product_id)
);
CREATE TABLE recipe_components (
    recipe_id varchar(150) NOT NULL REFERENCES recipe_versions(id),
    item_id varchar(100) NOT NULL REFERENCES stock_items(id),
    quantity numeric(20,6) NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (recipe_id, item_id)
);

CREATE TABLE preparations (
    id varchar(100) PRIMARY KEY,
    name varchar(200) NOT NULL,
    output_item_id varchar(100) NOT NULL REFERENCES stock_items(id),
    assumed boolean NOT NULL,
    notes text NOT NULL DEFAULT '',
    is_deleted boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX uq_preparation_name ON preparations (lower(trim(name))) WHERE NOT is_deleted;
CREATE UNIQUE INDEX uq_preparation_output ON preparations (output_item_id) WHERE NOT is_deleted;
CREATE TABLE preparation_versions (
    id varchar(150) PRIMARY KEY,
    preparation_id varchar(100) NOT NULL REFERENCES preparations(id),
    version integer NOT NULL CHECK (version > 0),
    effective_from timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    output_quantity numeric(20,6) CHECK (output_quantity > 0),
    assumed boolean NOT NULL,
    notes text NOT NULL DEFAULT '',
    UNIQUE (preparation_id, version)
);
CREATE TABLE preparation_inputs (
    preparation_version_id varchar(150) NOT NULL REFERENCES preparation_versions(id),
    item_id varchar(100) NOT NULL REFERENCES stock_items(id),
    quantity numeric(20,6) NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (preparation_version_id, item_id)
);

CREATE TABLE saved_questions (
    position integer PRIMARY KEY CHECK (position >= 0),
    question text NOT NULL
);

CREATE TABLE sales_reports (
    id varchar(100) PRIMARY KEY,
    report_date date NOT NULL UNIQUE,
    status varchar(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'reversed')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at timestamptz,
    issues jsonb NOT NULL DEFAULT '[]',
    CHECK ((status = 'draft' AND approved_at IS NULL) OR (status <> 'draft' AND approved_at IS NOT NULL))
);
CREATE TABLE sales_images (
    id varchar(100) PRIMARY KEY,
    report_id varchar(100) NOT NULL REFERENCES sales_reports(id),
    content_sha256 char(64) NOT NULL UNIQUE,
    source_path text NOT NULL,
    extraction jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sales_items (
    id varchar(100) PRIMARY KEY,
    report_id varchar(100) NOT NULL REFERENCES sales_reports(id),
    product_id varchar(100) REFERENCES products(id),
    raw_item_name text NOT NULL,
    quantity integer CHECK (quantity >= 0),
    recipe_id varchar(150),
    price_version_id varchar(150),
    unit_price_vnd bigint CHECK (unit_price_vnd >= 0),
    issues jsonb NOT NULL DEFAULT '[]',
    UNIQUE (report_id, product_id),
    FOREIGN KEY (recipe_id, product_id) REFERENCES recipe_versions(id, product_id),
    FOREIGN KEY (price_version_id, product_id) REFERENCES price_versions(id, product_id),
    CHECK ((recipe_id IS NULL AND price_version_id IS NULL) OR product_id IS NOT NULL)
);
CREATE TABLE sales_preparation_versions (
    sales_item_id varchar(100) NOT NULL REFERENCES sales_items(id),
    preparation_version_id varchar(150) NOT NULL REFERENCES preparation_versions(id),
    PRIMARY KEY (sales_item_id, preparation_version_id)
);

CREATE TABLE purchases (
    id varchar(100) PRIMARY KEY,
    purchased_at timestamptz NOT NULL,
    note text NOT NULL DEFAULT ''
);
CREATE TABLE purchase_items (
    id varchar(100) PRIMARY KEY,
    purchase_id varchar(100) NOT NULL REFERENCES purchases(id),
    item_id varchar(100) NOT NULL REFERENCES stock_items(id),
    quantity numeric(20,6) NOT NULL CHECK (quantity > 0),
    total_cost_vnd bigint CHECK (total_cost_vnd >= 0)
);
CREATE TABLE inventory_movements (
    id varchar(150) PRIMARY KEY,
    item_id varchar(100) NOT NULL REFERENCES stock_items(id),
    kind varchar(20) NOT NULL CHECK (kind IN ('opening', 'opening_adjustment', 'purchase', 'sale', 'waste', 'correction', 'reversal')),
    signed_quantity numeric(20,6) NOT NULL CHECK (signed_quantity <> 0),
    occurred_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sale_item_id varchar(100) REFERENCES sales_items(id),
    purchase_item_id varchar(100) REFERENCES purchase_items(id),
    reversal_of varchar(150) UNIQUE REFERENCES inventory_movements(id),
    note text NOT NULL DEFAULT '',
    CHECK (NOT (sale_item_id IS NOT NULL AND purchase_item_id IS NOT NULL)),
    CHECK ((kind = 'sale' AND sale_item_id IS NOT NULL AND signed_quantity < 0 AND purchase_item_id IS NULL AND reversal_of IS NULL) OR
           (kind = 'purchase' AND purchase_item_id IS NOT NULL AND signed_quantity > 0 AND sale_item_id IS NULL AND reversal_of IS NULL) OR
           (kind = 'waste' AND signed_quantity < 0 AND sale_item_id IS NULL AND purchase_item_id IS NULL AND reversal_of IS NULL) OR
           (kind = 'opening' AND signed_quantity > 0 AND sale_item_id IS NULL AND purchase_item_id IS NULL AND reversal_of IS NULL) OR
           (kind = 'reversal' AND reversal_of IS NOT NULL AND sale_item_id IS NULL AND purchase_item_id IS NULL) OR
           (kind IN ('opening_adjustment', 'correction') AND sale_item_id IS NULL AND purchase_item_id IS NULL AND reversal_of IS NULL))
);
CREATE UNIQUE INDEX uq_sale_consumption ON inventory_movements (sale_item_id, item_id) WHERE kind = 'sale';
CREATE UNIQUE INDEX uq_purchase_movement ON inventory_movements (purchase_item_id) WHERE kind = 'purchase';
CREATE INDEX ix_movement_stock_time ON inventory_movements (item_id, occurred_at);
CREATE INDEX ix_recipe_effective ON recipe_versions (product_id, effective_from);
CREATE INDEX ix_price_effective ON price_versions (product_id, effective_from);

CREATE VIEW inventory_balances AS
SELECT s.id AS item_id, s.name, s.unit, COALESCE(sum(m.signed_quantity), 0) AS quantity,
       (COALESCE(sum(m.signed_quantity), 0) < 0) AS negative_stock,
       (s.opening_stock IS NOT NULL) AS opening_stock_known
FROM stock_items s LEFT JOIN inventory_movements m ON m.item_id = s.id
WHERE s.kind <> 'prepared' AND NOT s.is_deleted
GROUP BY s.id, s.name, s.unit, s.opening_stock;
