# Draft conceptual database design

See `erd.svg`. This is a conceptual ER model, before relational schema translation.
Rectangles are entities, diamonds are relationships, ellipses are attributes,
and underlined attributes are keys. Each (min,max) label describes participation
of the adjacent entity instance, not the entity on the opposite side.

## Relationships

- A Product has zero or more Recipe versions; each Recipe belongs to exactly one Product.
- A Recipe contains one or more StockItems; a StockItem can occur in any number of Recipes. `amount_per_serving` belongs to this relationship.
- StockItem covers purchased ingredients, prepared bases, and packaging. A PreparationRecipe consumes one or more input StockItems and produces exactly one prepared StockItem with a specified output yield. Both recipes and preparation recipes are versioned.
- A SalesReport includes zero or more SalesLines; every SalesLine belongs to exactly one report.
- A draft SalesLine uses zero or one Recipe; approval requires exactly one. The chosen recipe also identifies the matched Product and preserves the recipe used for inventory deductions.
- A StockMovement affects exactly one purchased StockItem; a StockItem has zero or more movements. Prepared bases are intermediate recipe quantities, expanded into raw requirements before recording consumption.
- A SalesLine generates zero or more StockMovements; a movement has zero or one source SalesLine. Opening balances, purchases, waste and manual adjustments do not require a sale.

## Units and inventory

Each StockItem has one confirmed base unit: `g`, `ml`, or `piece`. Recipe amounts
and stock movements are recorded in that unit. Use decimal quantities, not float.
Convert kg to g and L to ml when entering purchases. Do not convert g to ml
without ingredient-specific conversion information. A preparation yield is an
explicit process conversion, not a general conversion between g and ml.

Confirmed units: milk-tea and tea bases use ml; all other edible ingredients use
g, including concentrated coffee and syrup. Cups, cones and straws use piece.

Raw consumption = prepared amount used / batch output yield * batch input amount.
Preparation recipes may have multiple raw inputs. Store frozen, versioned
derivation references with approved consumption. Never deduct both a prepared
base and its raw powder. This first design estimates raw usage from approved
sales; it does not also deduct powder when making a batch. Actual waste and
physical counts are separate adjustments.

Remaining inventory is derived from the sum of signed stock movements; avoid a
second independently maintained stock balance. Sales consumption is negative;
purchases and opening stock are positive. Approval creates deductions once;
corrections reverse previous movements before replacement deductions.

Products of different sizes are separate sellable products. M is 500 ml and L
is 700 ml. Only catalogued combinations are allowed; do not automatically scale
recipes for upsizing. Coffee has only M; Ba vuong milk tea has only L. Cone ice
cream has no drink size. Preserve Vietnamese
catalog names and original extraction text. Product aliases can be added when
matching is implemented. Do not invent selling prices from these recipe photos.

## Recipe photo extraction

The draft ingredient list is `data/private/ingredients_draft.csv`, excluded from
Git. The initial draft accidentally omitted Thach duong den; this is corrected.
The current catalog removes the cold milk-tea duplicate, adds raw powder placeholders
and packaging, and assigns units based on the user's confirmed rules.

- `Chanh` is one stock ingredient; slicing/crushing is preparation information.
- `Syrup hạt dẻ và bơ` is one ingredient; its caramel milk-tea quantity has been confirmed privately.
- Ignore `0.8` and use the printed hot milk-tea ml value, including for cold drinks. Ice and water are not tracked.
- Milk tea, green tea, black tea and ice cream are prepared from powders and water. Preparation yields are not known yet and must not be guessed. Concentrated coffee is directly purchased and used in g.
- Recipes with jelly or boba consume one large straw; other cup drinks consume one small straw. Each cup drink consumes one appropriately sized cup. Do not track lids. Cone ice cream provisionally consumes a cone and ice cream, with no cup or straw.
- Extra toppings are separate sellable products with their own recipes, once their report labels are known. Ignore sugar/customization changes and use standard recipes.
- Photo recipes form the current menu, plus cone ice cream. Sales labels still require an alias mapping to the catalog.

## Constraints for relational design

Enforce unique product catalog names (including variant/size), unique recipe
version per product, unique ingredient per recipe, and at most one SalesLine
per matched product per report. For this one-shop daily-report workflow, allow
one canonical report per date, containing all 3-4 photos of that report. Merge
overlapping photo sections before approval; repeated uploads cannot create
additional sales. Report quantities already exclude cancelled/refunded items.
Draft reports may be incomplete; approval requires a date, resolved recipes,
confirmed units, preparation yields (or explicitly accepted provisional assumptions), complete quantities and resolved
completeness issues. Incomplete recipes can be saved as drafts but are not
eligible for automatic raw-stock deductions.
Recipe versions used by approved sales must be immutable. Approval and movement
creation must share one transaction and prevent duplicate consumption movements.

Purchases are entered separately on an inventory-receipt page, with quantity,
unit and cost. Sales photos do not establish how much inventory was purchased.
Allow estimated negative stock with a warning. Add effective-dated PriceVersions
so price edits do not rewrite historical revenue. Menu, recipe and price edits
update records through the app; they do not change database tables automatically.
New stock-item units and future structural changes use explicit schema migrations.

## Current implementation

The `/setup` page reads and saves PostgreSQL when `SHOP_STORAGE=postgres`.
Private `data/private/shop_config.json` is the initial import source, including
the five saved questions; it remains a separate prototype store in JSON mode.
Tea-base assumptions are 500 g powder producing 2,000 ml; the separately
authorized ice-cream assumption is 500 g powder producing 2,000 g ice cream.
Both boba bases provisionally use 1,000 g dry boba producing 2,000 g served boba.
Jellies are ready to serve. Provisional toppings use 40 g per serving.
M/L drink prices are provisional; cone and topping prices remain unspecified.
All assumptions are editable and marked. PostgreSQL stores recipe, price and
preparation versions, configuration snapshots and starting-stock movements.
Sales drafts now retain extracted rows and source photos, support catalog matching
and manual corrections, and flag overlapping photos. Consumption previews and
atomic approvals now freeze version references and create raw-stock movements.
Inventory now supports purchases, physical counts, waste and low-stock levels,
with auditable ledger movements and retry-safe operations. A physical count also
establishes a known stock baseline. See `inventory-management.md`. Approval reversal now retains the original report and restores its actual sale
movements before an optional correction draft. Schema revision `0005` adds the
audit table and allows only one active report per date. See `sales-reversals.md`. See `consumption.md` for
calculation rules and `sales-drafts.md` for the draft flow,
`database-setup.md` for setup commands and `../migrations/versions/` for the schema.
