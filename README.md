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