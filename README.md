# BobaOps AI

BobaOps AI is an end-to-end operations and analytics platform for my mom's beverage shop.

The project is inspired by my mom's real business workflow. Every day, she manually enters sales for each item into a spreadsheet to calculate revenue, inventory, costs, and profit. While this works for day-to-day operations, the manual process makes it difficult to store and analyze detailed historical data, such as item-level sales over time. As a result, she can mainly track short-term changes in revenue and profit, but has limited visibility into deeper trends such as product performance, sales patterns, and inventory usage.

The goal is to turn that process into a simple, automated, more analytical:

**Upload → Review → Approve → Analyze**

## Workflow

```text
Daily Sales PDF
      ↓
PDF Extraction
      ↓
Structured Sales Rows
      ↓
Product Normalization & Matching
      ↓
Human Review for Uncertain Rows
      ↓
Validation & Duplicate Check
      ↓
Approve Import
      ↓
PostgreSQL
      ↓
Sales + Inventory + Cost Calculations
      ↓
Analytics Dashboard
      ↓
Grounded AI Assistant
```

## MVP Features

- PDF sales report extraction
- Product and variant matching
- Confidence-based human review
- Duplicate-import protection
- PostgreSQL data storage
- Recipe-based inventory deduction
- Purchases and inventory movements
- Revenue, ingredient cost, and estimated gross profit
- Daily, weekly, and monthly analytics
- AI assistant using controlled backend tools
- Automated tests, Docker, CI, and deployment

## Tech Stack

- **Backend:** Python, FastAPI, Pydantic
- **Database:** PostgreSQL, SQLAlchemy
- **Data:** Pandas
- **Frontend:** Next.js, TypeScript, Tailwind
- **Testing:** Pytest
- **Deployment:** Docker, GitHub Actions
- **AI:** LLM API with structured tool calling

## Development Approach

The project is built incrementally:

```text
Data Audit
→ Database
→ PDF Pipeline
→ Matching & Review
→ Inventory & Costing
→ Analytics
→ AI Assistant
→ Testing & Deployment
```

The priority is to build a reliable end-to-end workflow rather than adding unnecessary architectural complexity.

## Privacy

Real shop data may be used privately for development, but the public repository will contain only sanitized or synthetic data. Secrets, payment information, supplier-sensitive data, and proprietary recipe quantities will not be committed.

## Status

**In development**

## Run the image extraction prototype

The local Python backend serves a browser interface for uploading a sales photo,
reviewing extracted dates, item names, and quantities, and downloading JSON.
Extraction alone does not persist anything. Click **Save sales draft** to store
the report and source photo in PostgreSQL/private storage. Inventory is unchanged.

```sh
source /Users/ngocminh/Desktop/venvs/mixue-agent/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env  # Only if .env does not already exist
```

Set `GEMINI_API_KEY` in `.env`. Keep it private; `.env` is excluded from Git.
The default model is `gemini-3.5-flash-lite`, optimized for simple extraction.
The connection timeout is 20 seconds and response timeout is 180 seconds.
Keep your Google project on the free
tier without paid billing if you want to avoid charges; a free tier is subject
to account quotas and availability. The application cannot determine your
Google project's billing status.

```sh
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and choose a JPG, PNG, or WebP under 10 MB.
Click **Extract sales** to send the photo to Google Gemini. Photos and results
are persisted only when you explicitly save a draft. Free-tier Gemini inputs may be used by Google
to improve its products. Review results against the original photo; cropped or
unreadable rows need attention. This is a local development tool, without login.

The backend re-reads `.env` for each request. After adding your key, select a
photo again to refresh the interface's configuration status. One click makes
one provider request, with no automatic retry or paid fallback.

Run offline checks (no API key or provider calls required):

```sh
python3 -m unittest discover -s tests -v
```

## Database and shop setup

Follow [the database setup guide](docs/database-setup.md) to start local PostgreSQL
and pgAdmin, run migrations, and import your private catalog. With
`SHOP_STORAGE=postgres`, http://127.0.0.1:8000/setup reads and saves database records.
The original private JSON stays untouched as an import source. Explicit
`SHOP_STORAGE=json` retains the separate earlier prototype.

Shop setup edits products, prices, recipes, stock items, starting balances,
preparation yields and the five questions for your mom. Recipe, preparation-yield
and price edits create versions. An outdated tab cannot overwrite a newer revision.
Deleted records are archived to preserve historical references. Starting-stock
edits create ledger adjustments; they are not purchase transactions. Unknown
cone/topping prices and initial stock remain blank, and assumptions stay marked.
Stock units/types and preparation output identities are protected after import;
add new records when those identities change. Click **Download backup** to export
the current settings; full database backups are described in the setup guide.

Daily sales drafts can now be saved and edited at `/sales`; see
[the sales draft guide](docs/sales-drafts.md). Multiple photos for one date share
one report. Exact duplicate uploads are rejected, and overlapping product rows
remain separate with warnings until corrected. Conservative name/alias matching
links recognizable items to catalog products; unmatched rows remain editable.
Use **Preview consumption** to calculate raw ingredients and packaging from the
current recipes and preparation yields. Resolve missing products/recipes and
duplicate rows, acknowledge provisional assumptions, and confirm the report is
complete before approving. Approval freezes the calculation, recipe/yield/price
versions, and records inventory deductions in one transaction. Repeated approval
cannot deduct twice. Unknown starting stock and negative estimates are flagged.
See [the consumption guide](docs/consumption.md). The [Inventory page](http://127.0.0.1:8000/inventory)
shows balances, purchases, physical counts, waste, low-stock thresholds and history.
Physical counts record the difference from the ledger and establish a known
baseline, including a count of zero. Entry retries use request IDs to prevent
duplicate purchases. See [the inventory guide](docs/inventory-management.md).
Approved reports can be reversed with a reason and a reviewed stock preview.
Original reports remain in history; a correction draft can reuse their photos
and rows. See [the reversal guide](docs/sales-reversals.md).

Menu and recipe edits change records through the app. Structural schema changes
use Alembic migrations. Version dates currently use the save time; backdated
changes need a future effective-date interface.

## Analytics dashboard

Open [Dashboard](http://127.0.0.1:8000/dashboard) for approved sales trends,
product rankings, raw-ingredient usage, purchase/waste/correction summaries and
current stock warnings. Prices saved at approval determine estimated sales value;
missing prices and missing report dates are disclosed. No AI calls or new
packages are needed. See [how the dashboard works](docs/analytics.md).

## AI assistant

Open `/chat` for a Vietnamese-first, read-only assistant using OpenRouter and the
existing database. It can explain approved sales, ingredient usage, current stock
and recipes. Enter `OPENROUTER_API_KEY` in `.env`; `OPENROUTER_MODEL=openrouter/free`
uses free models, with at most two model requests per question and no paid
fallback. Answers include expandable source figures. See [the assistant guide](docs/assistant.md).

## Actual ice-cream production

Record daily ice-cream powder totals in Inventory, including powders in discarded leftovers. See [the workflow and configuration guide](docs/ice-cream-usage.md).

## Confirmed catalog and demo stock

The confirmed menu and synthetic carton purchase data can be imported together with historical records preserved. See [catalog import and verification](docs/confirmed-catalog.md). Purchases and stock in the demo dataset are invented examples.
