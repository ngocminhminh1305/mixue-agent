ALTER TABLE sales_reports ADD COLUMN revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0);
ALTER TABLE sales_items ADD COLUMN source_image_id varchar(100) REFERENCES sales_images(id);
ALTER TABLE sales_items DROP CONSTRAINT sales_items_report_id_product_id_key;
CREATE INDEX ix_sales_items_report ON sales_items (report_id);
CREATE INDEX ix_sales_images_report ON sales_images (report_id);
