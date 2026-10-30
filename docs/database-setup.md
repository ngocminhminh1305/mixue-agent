# Local PostgreSQL setup

Run these commands yourself from the project folder. No Docker service,
installation, migration, import, or test has been run by Codex for this change.

## 1. Install prerequisites

Install and open Docker Desktop for **Mac with Apple silicon** from
https://www.docker.com/products/docker-desktop/. PostgreSQL runs in Docker;
your Python backend stays in your existing virtual environment. Use your
existing Mac pgAdmin app to inspect the database.

```sh
source /Users/ngocminh/Desktop/venvs/mixue-agent/bin/activate
python3 -m pip install -r requirements.txt
```

The project database uses Mac port **5433**, leaving course PostgreSQL on 5432.
`POSTGRES_PASSWORD` is blank: this local container uses trust authentication.
The mounted `docker/pg_hba.conf` applies to both new and existing database volumes;
the database is published only on 127.0.0.1. Local applications can connect without
a password. Use password authentication before deployment or sharing network access.
Your Gemini key and Mac pgAdmin/course credentials are independent and unchanged.

## 2. Start the database and management interface

```sh
docker pull postgres:18
docker image tag postgres:18 mixue_agent_postgres:18
docker compose up -d --wait db
```

Database storage lives in the Docker-managed `postgres_data` volume, outside
the OneDrive project directory. A normal `docker compose down` keeps it.
Do not add `-v` unless you intend to delete database volumes.

If host port 5433 is occupied, choose another unused `POSTGRES_PORT` in `.env`.
Both Python and Docker read that setting. PostgreSQL's internal container port
stays 5432; it does not compete with the Mac course database.

To apply these changes to a previously started project container:

```sh
docker compose up -d --wait --force-recreate db
```

This keeps the existing PostgreSQL volume. Restart the backend afterward.
Do not re-import an already imported catalog. If the optional browser pgAdmin
container was started earlier, `docker compose stop pgadmin` stops that interface
without touching your Mac pgAdmin app or deleting its volume.

## 3. Create tables and import current settings

```sh
python3 -m alembic upgrade head
python3 -m scripts.import_shop_config
```

Migrations are repeatable. Import runs only into an empty catalog and refuses
to overwrite an already imported database. Private JSON and its backups remain
unchanged. No real shop data is included in the migration files.

## 4. Start the backend

Stop an older backend with Ctrl+C first, then run:

```sh
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000/setup. With `SHOP_STORAGE=postgres`, reads and saves
use PostgreSQL; if PostgreSQL is unavailable, saves fail with a clear message
rather than falling back to JSON. The extractor still uses Gemini as before.

Changing `.env` connection credentials requires a backend restart. Setting
`SHOP_STORAGE=json` explicitly restores the earlier JSON prototype, whose
data is separate from PostgreSQL after import.

## 5. View tables in your Mac pgAdmin app

Open your installed pgAdmin app. Register a **new** server connection named
**BobaOps project**; keep your course connection unchanged:

| Field | Value |
|---|---|
| Name | BobaOps project |
| Host name/address | `127.0.0.1` |
| Port | `5433` (or `POSTGRES_PORT` from .env) |
| Maintenance database | value of `POSTGRES_DB` |
| Username | value of `POSTGRES_USER` |
| Password | leave blank |

Explore Databases → your database → Schemas → public → Tables.
Use **View/Edit Data → All Rows** to inspect records. Make routine changes
through Shop setup so validation and recipe/price history are maintained.

The optional Docker browser pgAdmin service is separate from your Mac app.
Its web login still uses `PGADMIN_EMAIL` / `PGADMIN_PASSWORD`; those values do not
set your Mac app's master password or your course PostgreSQL password. If you
choose to start it, its database connection uses host `db`, port `5432`, and a
blank database password. It is not needed for the default setup above.

## What is connected now

- Current stock items and products, archived rather than physically deleted.
- Append-only recipe, preparation-yield and selling-price versions on relevant edits.
- Saved questions, revision history and protection against conflicting tab saves.
- Opening stock changes recorded as ledger movements; unknown stock remains an
  estimate until an opening balance or physical count establishes a baseline.
- Database tables for daily reports/images, sales rows, aliases, purchases and
  inventory movements.
- Save extracted sales as daily drafts, append photos, retain source images,
  match catalog products and edit rows at `/sales`. See `sales-drafts.md`.
- Preview ingredient usage and approve stock deductions once, freezing the
  calculation and recipe versions. See `consumption.md`; migration 0003 is required.
- Inventory balances, multi-item purchases, physical-count corrections, waste,
  optional warning thresholds and history at `/inventory`. Migration 0004 is
  required; see `inventory-management.md`.

Approved reports can now be reversed through Saved sales after schema revision
`0005`. See [the reversal guide](sales-reversals.md).
Multiple photos share one daily draft; overlaps are
flagged for manual review rather than automatically summed or discarded.
The frontend's opening-stock field edits starting balances; it is not a
purchase form. Existing extraction results are not automatically imported.

Stock-item unit/type and preparation output cannot be changed after import,
because old recipes refer to them. Add a new stock item/preparation and update
current recipes instead. Prices, quantities, names, availability and notes remain
editable. Version dates currently use the save time; backdated price/recipe
changes require a later effective-date interface.

## Checks you can run

Offline/unit tests:

```sh
python3 -m unittest discover -s tests -v
```

Database integration tests use a **separate test database** and temporary schema:

```sh
docker compose exec db createdb -U bobaops bobaops_test
BOBAOPS_TEST_DATABASE=bobaops_test python3 -m unittest discover -s tests -p 'test_postgres.py' -v
```

Replace `bobaops` above if you changed `POSTGRES_USER`. If the test database
already exists, skip `createdb`. Integration tests are skipped without the
explicit test-database variable; they never target the configured shop database.

## Backup

This command writes a private PostgreSQL backup without putting passwords in
the command line:

```sh
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > data/private/bobaops.dump
```
