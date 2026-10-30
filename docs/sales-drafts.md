# Saving and reviewing daily sales

## Apply the update

From the project folder, in your existing environment, run:

```sh
source /Users/ngocminh/Desktop/venvs/mixue-agent/bin/activate
docker compose up -d --wait db
python3 -m alembic upgrade head
```

Stop the old backend with Ctrl+C, then restart it so cached table metadata is
refreshed after the migration:

```sh
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

No new dependencies were added for this update. Do not re-import an existing
catalog or recreate its volume. Codex has not run services, migrations or tests.

## Use it

1. Open `/`, extract a photo, check/correct the report date, and click **Save sales draft**.
2. Open **Review saved draft**, or visit `/sales`. Correct quantities and receipt
   names, choose catalog products, and remove duplicate rows. Click **Save corrections**.
3. Repeat extraction/save for the remaining photos using the same date. They
   append to the same draft. After each new photo, reload any draft already open
   in another tab before editing it.

An identical photo is rejected even if submitted for another date. Different
photos can contain overlapping rows; both rows are retained and flagged. Compare
source photos before removing a duplicate. Quantities are never automatically
summed. A re-encoded or cropped photo may have a different hash, so photo hashing
does not replace this review.

To start over for a date, select its report and click **Delete draft** beside the
date. Save or reload pending edits first. Confirm the displayed date to delete
the whole draft, its rows and all source photos. The same image can then be saved
again. Approved reports and reports with inventory movements cannot be deleted
this way. A failed file cleanup is reported separately; database deletion still
releases the image hash. No reports are deleted by installing this update.

The extraction prompt omits `(MT)` but preserves actual size labels. Backend
matching applies Unicode normalization, case folding, whitespace cleanup and
`(MT)` removal to both receipt and catalog comparison keys. Display names are
unchanged. Final labels such as `(Size L)`, `(L)`, `size L` and standalone `L`
are interpreted as sizes; words such as `bigsize` remain part of the base name.

An explicit size must match that catalog size. With no receipt size, a unique
product matches its only size. If the base name identifies exactly one M and one
L product, no size label means **M**, according to this shop's receipt format.
Other ambiguous names/aliases remain unmatched. Inactive variants still count
when checking sizes; an inactive default M must not fall back to L. Automatic
matches only select active products. Accents remain significant; there is no
fuzzy guessing. Products absent from the catalog remain saved as unmatched draft
rows with their original names and quantities, ready to match after catalog updates.

New saves use this matcher. For drafts already saved, click **Match unmatched
products** beside Reload report. Save any pending corrections first. This action
only fills unmatched rows, preserves names, quantities and existing selections,
and uses revision checks. Matching makes no Gemini request. Save/edit operations
also make no Gemini requests. No migration is needed for the matching update;
restart the backend and refresh the frontend to load it.

Dates, missing names, quantities and unmatched products can be corrected in the
draft. A report needs a date to be saved, but its rows can remain incomplete.
The original extracted date and extraction warnings are preserved separately.
Changing a report's date is allowed only if no other active report uses it.

Saved reports survive backend restarts. Only drafts are editable. After resolving
the draft, use **Preview consumption** and the approval controls described in
`consumption.md`. Approval deducts stock once and locks the report. Changing
catalog data afterward does not recalculate approved sales. The Dashboard and assistant report approved sales only. Approved-report reversal is available through the
[reversal workflow](sales-reversals.md).

## Storage and technical connections

`frontend/index.html` sends the extracted JSON, chosen report date and original
photo to `POST /api/sales/drafts`. The `bobaops/sales/routes.py` router validates the data and
creates/appends a draft in a database transaction. `app.py` registers this router
and serves `frontend/sales.html` at `/sales`.

| Endpoint | Purpose |
|---|---|
| `POST /api/sales/drafts` | Save one extracted photo into its dated draft |
| `GET /api/sales/reports` | List the most recent 100 daily reports |
| `GET /api/sales/reports/{id}` | Load rows, product choices and source-photo links |
| `PUT /api/sales/reports/{id}` | Save corrections with the expected revision |
| `POST /api/sales/reports/{id}/match` | Match previously unmatched rows with the expected revision |
| `DELETE /api/sales/reports/{id}?revision=N` | Delete a draft and release its photo hashes |
| `GET /api/sales/images/{id}` | View a retained source photo |
| `GET /api/sales/reports/{id}/consumption` | Preview usage or view the frozen approved snapshot |
| `POST /api/sales/reports/{id}/approve` | Approve the reviewed preview and deduct stock atomically |

Migration `0002` adds a report revision and row-to-photo references. It removes
the per-report/product uniqueness constraint so unresolved overlapping draft
rows can be retained rather than silently lost. Approval requires those
duplicates to be resolved. Report locks and revision checks protect simultaneous
edits. Exact-image hashes have a database uniqueness constraint.

PostgreSQL stores reports, rows and original extracted JSON. Photos are stored
under ignored `data/private/sales_images/`, using generated filenames. Source
photos and original extraction remain even when draft rows are removed. Back up
**both the PostgreSQL database and that image folder**. Database backups alone do
not include the image files. File writes are cleaned up on ordinary transaction
failures; an interrupted process may leave an unreferenced image file.

There is no login yet. Continue using the local backend bound to `127.0.0.1`.

## Checks you can run

```sh
python3 -m unittest discover -s tests -v
```

`tests/test_sales.py` covers name/size matching, ambiguous aliases and sizes,
overlap warnings and invalid quantities. Database checks in `tests/test_postgres.py` cover
photo persistence, repeat-upload rejection, same-day appends, stale revisions,
corrections, rematching saved drafts, conflicting dates and protected approved reports. Follow the separate
test-database instructions in `database-setup.md`; integration checks are skipped
unless explicitly enabled.
