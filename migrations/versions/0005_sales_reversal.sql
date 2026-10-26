ALTER TABLE sales_reports DROP CONSTRAINT sales_reports_report_date_key;
CREATE UNIQUE INDEX uq_active_sales_date ON sales_reports(report_date) WHERE status <> 'reversed';
ALTER TABLE sales_images DROP CONSTRAINT sales_images_content_sha256_key;
ALTER TABLE sales_images ADD CONSTRAINT uq_report_image_hash UNIQUE (report_id, content_sha256);
CREATE INDEX ix_sales_image_hash ON sales_images(content_sha256);
CREATE TABLE sales_reversals (
    id varchar(100) PRIMARY KEY,
    report_id varchar(100) NOT NULL UNIQUE REFERENCES sales_reports(id),
    report_revision integer NOT NULL CHECK (report_revision >= 0),
    request_hash char(64) NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    reversed_at timestamptz NOT NULL,
    replacement_report_id varchar(100) UNIQUE REFERENCES sales_reports(id) ON DELETE SET NULL,
    snapshot jsonb NOT NULL,
    CHECK (replacement_report_id IS NULL OR replacement_report_id <> report_id)
);
