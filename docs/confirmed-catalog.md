# Confirmed catalog and demo inventory

The catalog importer reads private confirmed product recipes, preparation recipes and the explicitly synthetic inventory JSON. These files are intentionally excluded from Git. The demo Excel and JSON contain invented carton sizes, prices, balances and warning levels; they are not actual shop purchases.

## Import workflow

1. Back up PostgreSQL with `pg_dump` before applying an import.
2. Run `python3 -m alembic upgrade head` for schema 0007.
3. Run `python3 import_confirmed_catalog.py` to validate sources and write a private review plan, without changing catalog data.
4. Run `python3 import_confirmed_catalog.py --apply` to apply the reviewed data.
5. Restart the backend so its reflected database schema includes the new columns.

The default private source locations are `data/private/confirmed_catalog/` and `data/private/demo/inventory_2026-10-30.json`. Alternate locations can be supplied with `--sources` and `--demo`.

The imported catalog has 52 main products, 8 extra toppings, 34 purchased ingredients/packaging records and 11 preparation records, including the manually tracked mixed ice cream. Each extra topping costs 3,000 VND and uses its confirmed portion. Prices and recipes remain editable through Shop setup.

## Units, cartons and preparation

Purchased ingredients use g or ml. Packaging uses whole pieces. Purchase metadata stores the carton unit, base-unit quantity per carton, total carton price and inner packaging description. Unit cost is carton price divided by carton quantity, rounded to six decimal places. Saving edited carton quantities or prices recalculates that unit cost. Purchases on Inventory continue to accept base units; convert the number of cartons to g/ml/pieces using the saved carton quantity.

Preparation yields use the user's input-sum convention for each standard batch. Original boba retains the previously confirmed approximately 1,840 g usable drained yield. Water and ice are not stock items. Milk-tea recipes use the confirmed cold base with their existing 160/200 ml quantities, including Bá Vương at 160 ml. Ingredients with no supplied preparation are ready to serve.

Cup type is represented by its packaging component, including the U 400 ml cup. It is independent of the receipt's M/L matching label. Coffee retains its M receipt label and never gets an L variant. Sundaes have their own category and appear separately in dashboard counts and product filters.

Ice-cream powders are recorded through actual daily production totals. Batch recipes remain as reference. Unknown cone ice-cream net weights are stored as null, permitted only for manually tracked ice cream. They never become invented gram quantities or silently skip automatic ingredients. Mixed ice cream needs no guessed flavour ratio. Cups, cones and toppings still deduct normally. Matcha concentrate for drinks remains automatic.

## History, stock and retries

The import runs in one transaction: catalog/price/preparation versions, aliases, demo stock counts, low-stock thresholds and the import marker commit together. Failure rolls back all of them. A source-content fingerprint makes repeating an identical import a no-op, including after stock has changed. A changed source bundle represents a new import and applies its supplied balances again; review it carefully.

Compatible existing IDs are reused. Replaced products, obsolete stock and preparations are archived. A stock item's unit or type is never changed in place: a corrected identity is created when necessary. Existing sales, frozen approval snapshots, movements and purchase history are retained. Demo balances are explicit synthetic physical-count corrections at import time, not fabricated historical purchases. Later reversals of older reports retain the existing physical-count warnings.

Product assumptions remain separate from demo stock assumptions. Confirmed selling prices and recipes are retained while purchased stock is marked as demo/assumed. The app still requires review before approving sales. Test receipts are exercised in a separate test database, never inserted into the live shop database by verification.

## Verification

Run `BOBAOPS_TEST_DATABASE=bobaops_test python3 -m unittest discover -s tests -v` against a separate test database. Tests cover size matching, cartons, nullable manual ice-cream weights, transaction rollback, retry safety, preserved approvals, U-cup deductions, separate sundae analytics and the receipt approval flow without external model calls.
