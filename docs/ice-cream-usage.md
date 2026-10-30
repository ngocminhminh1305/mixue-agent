# Actual ice-cream powder usage

Ice-cream powders are deducted from actual daily production totals, including powder in ice cream left over and discarded. Selling fewer cones does not restore powder already mixed.

## Daily workflow

1. Open Inventory → Powders used for ice cream and select the production date.
2. Enter the total grams of each powder used for ice cream that day. Save one row or all entered rows.
3. Leave a field blank to keep its saved value. Enter zero to correct a saved total to zero.

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

- `ice_cream_usage.py`: daily total API, stock movements, concurrency and duplicate safeguards.
- `frontend/ice-cream-usage.js` and `frontend/inventory.html`: daily entry table.
- Migration `0006_ice_cream_usage`: powder flags, versioned deduction mode, daily totals and ledger operation type.
- `inventory.py`: manual preparation behavior; `analytics.py`: actual daily usage.

Run migrations before restarting the backend. Tests use a separate test database. This feature does not import the pending confirmed product catalog or invent production quantities.
