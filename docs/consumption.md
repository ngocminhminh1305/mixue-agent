# Consumption preview and approval

## Apply the update

In your existing project environment, run these commands yourself:

```sh
docker compose up -d --wait db
python3 -m alembic upgrade head
```

Stop and restart the backend after migration to refresh its table metadata:

```sh
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

No new dependencies or catalog re-import are needed. Migration 0003 adds a JSONB
column for approved calculation snapshots; existing drafts remain untouched.
Codex has not started services, migrated your database or run tests for this change.

## Use it

Open `/sales`, choose a saved daily report, save corrections, and click **Preview
consumption** beside the date. This action is read-only. It shows ingredient and
packaging usage, current balances, projected balances, provisional assumptions,
and issues to resolve before approval. Missing starting stock is shown as
**Unknown**, with a separately labeled ledger estimate; it is never treated as a
known zero balance. A physical count on `/inventory` establishes a known baseline,
including a count of zero. Negative ledger balances are warnings, not automatic blockers.

Unmatched products, missing quantities, missing/empty recipes, missing preparation
inputs/yields, archived ingredients, fractional pieces and overlapping product
rows block approval. The preview may show partial known usage while blocked;
that subtotal is explicitly labeled incomplete. Keep missing-catalog rows in the
draft and add their products/recipes later rather than silently skipping them.

After resolving issues, confirm all rows/photos were reviewed and the report is
complete. Acknowledge any provisional recipes, yields or prices shown, then click
**Approve and deduct inventory** and confirm the date. Approval locks the report:
editing, photo appends and deletion are rejected. Use the [reversal workflow](sales-reversals.md) to retain the original and
restore its recorded deductions before reviewing a correction draft.
Opening an approved report shows the saved calculation and its balances **at
approval**, not today's stock. Future catalog edits do not rewrite that snapshot.

## Calculation rules

Direct ingredient/packaging usage is sold quantity multiplied by recipe quantity.
Prepared bases are expanded into raw ingredient equivalents:

```text
raw usage = drinks sold × prepared quantity per drink ÷ batch output × raw batch input
10 drinks × 160 ml milk tea ÷ 2,000 ml output × 500 g powder = 400 g powder
10 drinks × 80 g served boba ÷ 2,000 g output × 1,000 g dry boba = 400 g dry boba
```

Only the raw powder/dry boba is deducted; prepared bases are not deducted again.
Ready-to-serve jelly is consumed directly. Cups, straws and cones are ordinary
recipe components in pieces. Water/ice are not invented as ingredients. Current
preparation validation supports raw inputs only, not nested preparation chains.

Calculations use Decimal arithmetic. Each sale row's total per raw ingredient is
rounded to six decimal places with ROUND_HALF_UP. Report totals sum those exact
rounded movements, so preview totals agree with the ledger. Piece quantities must
be whole. A zero-quantity sale emits no movements but retains its recipe references.

For now, previews use the **current catalog at approval**, including for earlier
receipt dates. They do not claim to reconstruct recipes effective on the sales
date. Approval pins the selected recipe, preparation and price version IDs and
the calculated deductions. Unknown selling prices are warnings and remain null;
they do not prevent inventory calculations. Revenue analytics are separate work.

## How the files connect

`frontend/sales.html` requests a preview from the `bobaops/sales/routes.py` router. `bobaops/inventory/calculations.py`
loads catalog versions and ledger balances, calculates consumption, and produces
a preview token identifying the reviewed state. The approval request includes
that token, report revision, completeness confirmation and assumption acceptance.

Inside one database transaction, `bobaops/sales/routes.py` locks the report, holds the shared
catalog revision lock, locks stock rows in ID order, and recalculates the plan.
Changed sales, catalog revision or relevant stock balances invalidate the token;
the user must preview again. It stores version links, negative sale movements,
the snapshot and approved status together. Any failure rolls everything back.
The report status lock and per-sale-row/ingredient movement uniqueness prevent
double deductions, including concurrent approval requests. No Gemini call is used.

The Inventory purchase/count/waste workflows use the same stock-row locking
protocol before changing ledger balances. Future inventory writers must do so too.
Manual SQL outside the application can
bypass these application rules. Existing setup writes take the exclusive catalog
revision lock, so opening-stock or recipe edits cannot race an approval.

Stock movement timestamps record approval time, while the associated report keeps
the sales date. Starting-stock entries are treated as the ledger's opening baseline;
this is a current-balance model, not a dated stock reconciliation tool yet.

## Checks you can run

```sh
python3 -m unittest discover -s tests -v
```

`tests/test_inventory.py` covers conversion, packaging, boba, missing data,
unknown balances, zero sales and precision. Opt-in PostgreSQL checks in
`tests/test_postgres.py` cover atomic approval, saved versions, repeated/concurrent
approval, stale previews, blockers, confirmations and rollback after partial writes.
Use the separate test database instructions in `database-setup.md` to enable those.
