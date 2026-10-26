# Analytics and dashboard

Open http://127.0.0.1:8000/dashboard after starting the existing app:

```sh
source /Users/ngocminh/Desktop/venvs/mixue-agent/bin/activate
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

No new packages or schema migrations are introduced by the dashboard. The database
must already have the inventory migrations and `SHOP_STORAGE=postgres` configured.

## How the files connect

1. `frontend/dashboard.html` requests `GET /api/analytics/dashboard`.
2. `app.py` serves that page and registers the router from `analytics.py`.
3. `analytics.py` runs SQLAlchemy SELECT queries against PostgreSQL. It groups
   approved sales by date/product and sums recorded inventory deductions. One
   repeatable-read transaction gives a consistent view across the queries.
4. The endpoint returns JSON. The browser fills tables and uses Chart.js to draw
   the charts. Date changes repeat this read-only request. Gemini is not involved.
5. `inventory.py` includes category and price-assumption metadata in new approval
   snapshots so later catalog edits do not reclassify those approved sales.

SQL does the database selection and stock aggregation; Python handles summary
rules and missing data; HTML/CSS provide layout; JavaScript fetches JSON and draws
charts. No Tableau or Power BI service is required.

## Reading the numbers

- Only approved sales contribute to sales totals. Drafts remain visible in daily
  coverage. Missing report dates are gaps, not assumed zero sales.
- Default dates cover 30 days ending on the latest approved report, or today when
  none exists. This makes older test receipts visible. Last 7/30 days use today.
  Custom ranges can span at most 366 days.
- Drinks, cones and extra toppings are separate counts.
- Estimated sales value uses each approved row's saved unit price. Changing menu
  prices does not reprice old sales. Provisional prices are identified. Unknown
  prices are excluded with a warning; all prices unknown shows an unknown total.
  This estimate does not capture discounts, actual receipt takings or profit.
- New approvals freeze product category, name and price provenance. Older
  approvals without category metadata use the current catalog category with a
  warning; existing records are not rewritten.
- Ingredient usage uses the report's sales date and recorded raw-ingredient and
  packaging deductions, including preparation yields frozen at approval.
  Grams, millilitres and pieces are never added together.
- Purchases use the entered purchase date (legacy records fall back to recorded
  time). Waste, opening adjustments and physical-count corrections use recorded
  timestamps. Physical counts include events that needed no ledger adjustment.
- Current inventory always means **now**, regardless of the sales date filter.
  Unknown starting balances remain unknown even if their ledger is negative.
- Known purchase costs are shown with missing-cost warnings. They are not cost
  of goods sold; profitability is not calculated.

Recorded timestamp boundaries use `SHOP_TIMEZONE`, default `Asia/Singapore`.
For shop-local dates in Vietnam, set `SHOP_TIMEZONE=Asia/Ho_Chi_Minh` in `.env`
and restart. Sales dates and explicit purchase dates do not shift with timezone.

## Verification you can run

Run the full suite (database checks skip unless explicitly enabled):

```sh
python3 -m unittest discover -s tests -v
```

All 83 tests passed on 2026-10-23, including 34 PostgreSQL integration tests
against the separate `bobaops_test` database. No Gemini calls were made.

Database checks are opt-in and require a separate database ending in `_test`, as
described in the database setup guide. They must never target the shop database.
Check manually: approve a report, open Dashboard, verify its count, then change
its menu price and confirm the approved report's estimated value stays unchanged.
Use a date with no approved report to check that it displays a gap.

Chart.js 4.5.1 is bundled under `frontend/vendor/chartjs` with its MIT license.
The browser loads it locally through `/assets/chartjs-4.5.1.js`, so no chart CDN
request is needed. Its script-tag integration follows the
[official documentation](https://www.chartjs.org/docs/latest/getting-started/integration.html).
Tables remain available if chart loading fails.

Reversed reports do not contribute to approved sales or ingredient usage. Daily
coverage prefers the active correction report for a shared date. Inventory
activity includes stock restorations separately as reversals on their recorded
date. See [the reversal guide](sales-reversals.md).
