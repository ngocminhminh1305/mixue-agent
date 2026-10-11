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
It does not save reports or update inventory yet.

```sh
source /Users/ngocminh/Desktop/venvs/mixue-agent/bin/activate
python -m pip install -r requirements.txt
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
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and choose a JPG, PNG, or WebP under 10 MB.
Click **Extract sales** to send the photo to Google Gemini. No photos or results
are persisted by the application. Free-tier Gemini inputs may be used by Google
to improve its products. Review results against the original photo; cropped or
unreadable rows need attention. This is a local development tool, without login.

The backend re-reads `.env` for each request. After adding your key, select a
photo again to refresh the interface's configuration status. One click makes
one provider request, with no automatic retry or paid fallback.

Run offline checks (no API key or provider calls required):

```sh
python -m unittest discover -s tests -v
```
