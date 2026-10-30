# Inventory balances, purchases and physical counts

## Apply this update

Run these yourself in the activated project environment:

```sh
docker compose up -d --wait db
python3 -m alembic upgrade head
```

Stop the existing backend with Ctrl+C and restart after migration, then refresh
the browser. Restarting refreshes cached database table metadata.

```sh
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000/inventory. No new Python dependencies or catalog import
are needed. Codex has not executed services, migrations or tests for this change.

## Daily use

The stock list shows raw purchased ingredients and packaging. Remaining quantity
is the sum of stock movements. Without an opening balance or physical count,
the quantity remains **Unknown**, and the ledger estimate is shown separately.
Entering purchases alone does not claim that prior starting stock was zero.

Choose an action under **Update inventory**:

The four-option dropdown shows an editable table of **all** purchased ingredients
and packaging. Each row has its own Save/Apply button. **Apply to all entered rows**
is also available above and below the table. Enter only the rows you want to
change: blank rows stay untouched, while an explicit zero is valid for counts and
warning levels. Search does not discard edits or exclude entered rows from the
bulk save. A single-row save preserves the other unsaved rows and their original
balance tokens; newer stock activity still requires a refresh/recount.

- **Bought supplies:** enter purchase date, one or more stock items and quantities
  received. Each item appears once; combine multiple packages into that quantity.
  Optional cost is the total VND for that line, not a per-unit price. The entire
  purchase and its positive stock movements commit together.
- **Counted the actual remaining stock:** enter the actual total counted, not the
  difference, and a reason. If the ledger shows 1,200 g and you count 1,100 g, the
  app records a -100 g correction. A zero-difference count is still recorded in
  history and can establish a previously unknown baseline. Actual count zero is valid.
- **Waste, spill or damage:** enter the quantity lost and reason. This records a
  negative movement. Waste does not count as a sale or rewrite sales quantities.
- **Set a low-stock warning level:** choose the item and threshold. A balance at
  or below it is flagged. Tick **Turn off** to disable a level; blank leaves the
  existing level unchanged. Thresholds are not invented
  automatically. Unknown balances get an estimated warning rather than a claim of
  known low stock.

Use base units: grams, millilitres or pieces. Convert 1 kg to 1,000 g and 1 litre
to 1,000 ml yourself; no g/ml density conversion is inferred. Pieces must be whole.
Purchase costs do not overwrite the catalog's cost-per-unit assumptions.

Physical counts establish a baseline **at entry time**. Record pending sales and
purchases first, then count. If stock changes while the form is open, refresh and
recount instead of overwriting the newer activity. After the first physical count,
the opening-stock field in Shop setup is protected; use Inventory for corrections.

## Prepared bases: current model limitation

Prepared tea, ice cream and served boba are expanded into raw ingredient usage
when drinks are approved. Preparation production and prepared-stock batches are
not tracked separately yet. Therefore, when counting a raw ingredient that is also
held in a prepared base, include its remaining raw-ingredient equivalent. Counting
only unopened/raw powder while ignoring prepared tea would deduct that powder
again as the remaining drinks sell.

For example, with the provisional 500 g powder → 2,000 ml tea yield, 1,000 ml
prepared tea represents 250 g powder. A raw-equivalent count combines raw powder
remaining plus that equivalent. This also applies to dry/served boba and ice-cream
powder. Directly used syrups, ready-to-serve jelly and packaging do not need this
conversion. The count form explains this limitation; a future prepared-batch
inventory flow can handle it automatically.

## History and reliability

Recent stock movements show purchases, sales deductions, corrections, waste and
opening entries. Filter by stock item. Sales entries link back to the approved
report. A separate physical-count list includes counts with zero adjustment.
Names may reflect the current catalog; historical quantities, reasons and IDs are
retained. There is no delete/reversal UI for inventory entries; correct mistakes
through a new physical count with an explanation.

The browser assigns a request UUID and retains it while retrying unchanged data.
The server commits that UUID, a request fingerprint and the result in the same
transaction as the movements. Repeating an identical request returns the stored
result without adding stock again. Reusing an ID for different data is rejected.
After a lost network response, retry the unchanged entry rather than creating a
different one. Browser retry IDs survive refresh within the same tab when session
storage is available; they do not constitute cross-device duplicate receipt detection.

Inventory writers hold the catalog revision's shared lock, then lock affected
stock rows in ID order, matching sales approval's lock protocol. Physical counts,
waste and warning-level edits also check a balance token including ledger/count
history, preventing stale forms from silently overwriting newer activity. Inventory
changes relevant to a sales consumption preview require that preview to be refreshed.

Balances may be negative and are clearly flagged. Corrections represent observed
differences; the app does not infer whether every difference was waste or a recipe
variation. The purchase date is stored separately from the entry timestamp. Stock
is a current ledger, not a backdated reconciliation engine.

## Files and schema

`app.py` serves `frontend/inventory.html` at `/inventory` and registers the router
from `bobaops/inventory/routes.py`. That module uses `bobaops/core/database.py` connections and Decimal
quantities. Migration 0004 adds optional warning levels and purchase dates,
`inventory_operations` for retry-safe requests, and `stock_counts` for observed
counts. The `inventory_balances` view recognizes a count as a known baseline.
`bobaops/inventory/calculations.py` uses that same baseline when previewing sales consumption.
`bobaops/catalog/repository.py` protects opening balances after a physical count. Existing
products, recipes, sales snapshots and movements are retained.

| Endpoint | Purpose |
|---|---|
| `GET /api/inventory` | Current balances, warnings and balance tokens |
| `POST /api/inventory/purchases` | Atomic multi-item purchase and movements |
| `POST /api/inventory/counts` | Physical count and delta correction |
| `POST /api/inventory/waste` | Negative waste movement with a reason |
| `POST /api/inventory/thresholds` | Optional low-stock threshold |
| `POST /api/inventory/counts/batch` | Atomic multi-row physical counts |
| `POST /api/inventory/waste/batch` | Atomic multi-row waste entries |
| `POST /api/inventory/thresholds/batch` | Atomic multi-row warning-level changes |
| `GET /api/inventory/history?item_id=...` | Recent movements and physical counts |

Thresholds and count history are in PostgreSQL, not the earlier JSON catalog
export. Use a full database backup to preserve them. No Gemini call is made by
these workflows. Continue running locally; production authentication and secure
database access are separate deployment work.

## Checks you can run

```sh
python3 -m unittest discover -s tests -v
```

`tests/test_inventory_api.py` covers quantities, request validation and piece rules.
Opt-in PostgreSQL checks in `tests/test_postgres.py` cover purchases, idempotent
retries/concurrency, count baselines, corrections, stale counts, waste history,
zero-delta counts, thresholds, transaction rollback and sales-preview invalidation.
Enable the separate test database as described in `database-setup.md`.

The editable-table update requires no additional migration beyond 0004. Restart
the backend and refresh Inventory. Bulk saves use one parent request record;
each physical count also keeps its own child audit record, preserving the current
count-to-operation foreign key. A failed/stale row rolls back the whole save.
Unchanged retries return the original result without repeating any entries.

Approved sales can be reversed from Saved sales; their original deductions are
restored once and linked in stock history. This is separate from correcting a
purchase, waste entry or physical count. See [sales reversals](sales-reversals.md).
