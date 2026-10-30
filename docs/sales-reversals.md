# Reversing an approved sales report

Open **Saved sales**, choose an approved report, then select
**Reverse approved report…**.

1. Review the ingredients/packaging to add back and current stock after reversal.
   Enter a reason and confirm the stock impact and any physical-count warnings.
2. Leave **Create a correction draft** checked to reuse the original photos,
   quantities and product matches. Click **Reverse report and restore stock**.
3. Review the new draft, correct its quantities/matches/date, then preview and
   approve it separately. Its approval uses the current recipes, yields and prices.

Creating a correction draft requires no Gemini or OpenRouter call. Uncheck the
option when you only want to cancel the old report; you can upload the same photos
again afterward. A photo already present in an active report is still rejected.

## What is preserved

The original report remains read-only with status `reversed`. Its sales rows,
source photos, approved prices, recipe/yield references and approval snapshot stay
intact. The reversal adds an audit row with reason, time, request ID, reviewed
preview and a link to the correction report. Reports link back to their originals.
Repeated corrections create a chain of retained originals; at most one report
per date can be draft or approved. Reversed history can share that date.

Stock is restored by negating each original recorded sale movement, linked by
`reversal_of`. The system never recalculates the reversal with today's recipes.
Unknown opening stock remains unknown. Zero-quantity sales can be reversed without
inventing stock movements. Archived ingredients can still receive their historical
stock restoration.

Reversed reports are excluded from approved sales and ingredient usage in
Dashboard and the assistant. Their replacements contribute only after approval.
Stock history shows each restoration, its reason and the original report link.
Inventory activity shows reversals separately on their recorded date; sales charts
continue to use receipt dates. Old approval balances are historical snapshots.

## Stock counts and concurrency

If an affected item has been physically counted after approval, the preview warns
that its current ledger will change. Reversal does not undo that physical count or
assert a new physical quantity. After reviewing and approving the corrected sales,
count the item again if needed. For example: a later count of 800 g followed by a
120 g reversal makes the ledger 920 g; it does not mean a new count found 920 g.

Preview tokens include the original movements, current balances and count
observations. Changed stock invalidates the preview, requiring another review.
Approval, reversal, catalog and inventory operations use a consistent lock order.
The restoration, report status, correction draft and audit record commit together
or roll back together. Retrying the same request ID and payload returns the saved
result; a different payload with that ID is rejected. `reversal_of` is unique, so
an original deduction cannot be restored twice.

Correction photos use separate database identities pointing to the same immutable
files. Deleting a correction draft deletes only its records; files referenced by
the retained original are kept. Deleted correction links become null in the audit
record. Approved and reversed reports cannot be deleted through the app.

## Migration and verification

Schema revision `0005` replaces the old globally unique sales date with a unique
active-date index. Image hashes become unique per report; upload code serializes
matching hashes and checks active reports to retain duplicate-upload protection.
The new `sales_reversals` table stores audit records. No existing sales or stock
quantities are rewritten by the migration.

```sh
python3 -m alembic upgrade head
```

Restart Uvicorn after migration so its reflected table cache includes the new
schema. Back up PostgreSQL before upgrading; restore a backup rather than dropping
reversal history to downgrade. Reversing a report is a business action distinct
from rolling back a database migration.

Tests use a separate `bobaops_test` database and mock all model calls:

```sh
BOBAOPS_TEST_DATABASE=bobaops_test python3 -m unittest discover -s tests -v
```

They cover retained history, changed recipes, independent correction approvals,
retry/concurrent request safety, atomic rollback, count warnings, unknown stock,
zero sales, retained photos, reuploads and repeated corrections.

## Files implementing this stage

- `bobaops/sales/reversals.py`: preview, validated reversal transaction, correction cloning.
- `bobaops/sales/routes.py`: active-date/photo handling, reversal metadata and safe photo cleanup.
- `migrations/versions/0005_sales_reversal.py` and `.sql`: schema migration.
- `app.py`: reversal router registration.
- `bobaops/inventory/routes.py`: restoration links in stock history.
- `bobaops/analytics/routes.py`: active correction coverage and separate reversal activity.
- `bobaops/assistant/routes.py`: reversal activity description for its existing read-only tool.
- `frontend/sales.html`: reversal review and correction/history links.
- `frontend/dashboard.html`, `frontend/chat.html`: display reversal activity.
- `tests/test_reversals.py`, `tests/test_postgres.py`, `tests/test_analytics.py`:
  validation, database and reporting regression checks.
- README and database, sales, consumption, analytics and inventory guides:
  workflow and schema documentation.
