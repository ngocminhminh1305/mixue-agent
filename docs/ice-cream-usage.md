# Actual ice-cream powder usage

Ice-cream powders are deducted from actual daily production totals, including powder in ice cream left over and discarded. Selling fewer cones does not restore powder already mixed.

## Daily workflow

1. Open Nhập doanh số and enter the total daily grams of Bột kem nguyên vị. Enter zero if none was used.
2. Add other powders, such as matcha for ice cream, only when needed.
3. Extract the receipt and check its date. Powder usage uses that date automatically.
4. Save the draft. The receipt and daily powder totals commit together. Other recipe-based deductions still wait for report approval.

Every displayed powder row requires a quantity. Unselected optional powders remain unchanged. There is no separate date input for powders. The original daily-usage API remains available for existing clients and corrections.

These are daily totals, not additional quantities. Changing 1,500 g to 2,000 g deducts only another 500 g. Repeating an identical save does not deduct again. Concurrent or stale edits require a reload. A note and movement history preserve corrections.

Do not enter discarded prepared ice cream as additional raw-powder waste: its powder is already included in the production total. Record production before doing a physical count. Count remaining unused raw powders; do not include prepared ice cream in those powder counts.

## Recipe and stock configuration

Flag raw ingredients measured in grams as ice-cream powders in Shop setup. For each ice-cream preparation, select “Record ice-cream powders manually each day”. This setting is versioned. Production entry does not require a batch yield or a vanilla/matcha mix proportion. Any preparation inputs retained for reference must be flagged raw powders.

Products still consume their packaging, toppings, and other recipe components when approved. Matcha used by an automatically calculated drink concentrate still follows its preparation recipe; enter only matcha used for ice cream in the manual table. The powder flag does not globally disable automatic deductions.

Only purchased powders are entered here. Recipes and preparation quantities can remain available as reference information.

## Sales, history and analytics

Reversing a sales report restores its original sale deductions. It does not restore powders actually used for ice-cream production. Edit the daily powder total separately only if that production record was wrong.

The ingredient-usage dashboard combines approved recipe deductions with actual daily ice-cream totals, using production date for the latter. Corrections remain visible in stock history. Unknown opening stock stays unknown until counted.

Older approved reports retain their original automatic deductions. The backend blocks positive manual production totals on dates with those approved reports, and blocks new automatic ice-cream approvals where manual totals already exist. To correct such a date, reverse the old report, approve its correction using manual ice-cream preparations, then record actual production. Check physical stock if changing historical entries after a stock count.

## Files and migration

- `bobaops/inventory/powder_usage.py`: daily total API, stock movements, concurrency and duplicate safeguards.
- `frontend/receipt-powders.js` and `frontend/index.html`: receipt-linked daily entry. `frontend/ice-cream-usage.js` is the earlier standalone view.
- Migration `0006_ice_cream_usage`: powder flags, versioned deduction mode, daily totals and ledger operation type.
- `bobaops/inventory/calculations.py`: manual preparation behavior; `bobaops/analytics/routes.py`: actual daily usage.

Run migrations before restarting the backend. Tests use a separate test database. The page reorganization needs no new schema migration and does not invent production quantities.
