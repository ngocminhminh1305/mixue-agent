# Read-only AI assistant

Open `/chat` in the running application. The new **Trợ lý AI** navigation link
appears on extraction, setup, sales, inventory and dashboard pages.

## Configuration

Add your key locally in `.env` (never commit it):

```dotenv
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=openrouter/free
```

The backend re-reads these values on every question. `openrouter/free` selects
available free models supporting the required tools. You can instead specify an
explicit model ID ending in `:free` that supports tool calling. Paid IDs are
rejected before making a request. No paid fallback list or paid plugins are sent.
Availability of free models can change. See OpenRouter's
[free router](https://openrouter.ai/openrouter/free),
[free variants](https://openrouter.ai/docs/guides/routing/model-variants/free), and
[tool calling](https://openrouter.ai/docs/guides/features/tool-calling) documentation.

Gemini remains responsible for receipt-image extraction. The assistant uses
OpenRouter and the existing PostgreSQL database. It needs no new dependency,
API database service, migration or data import.

If the app is not already running:

```sh
source /Users/ngocminh/Desktop/venvs/mixue-agent/bin/activate
python3 -m uvicorn app:app --host 127.0.0.1 --port 8006 --reload
```

Then open http://127.0.0.1:8006/chat. Port 8006 is an example to avoid the other
local project's services on ports 8000–8005; the same page works on whichever
port you choose for this app.

## What it can answer

- Approved sales for a specific date range, product rankings and daily trends.
- Raw ingredient and packaging usage recorded for approved sales.
- Purchases, waste and stock corrections for a date range.
- Current inventory, low-stock thresholds, unknown balances and negative ledgers.
- Current active products, sizes, prices, per-serving recipes and preparation
  yields, with their stored assumption flags.

Examples: “Ngày 17/09/2026 bán được bao nhiêu đồ uống?”, “Nguyên liệu nào đang
sắp hết?”, “Công thức trà sữa trân châu size L là gì?” Relative dates are resolved
using the server-supplied current time and `SHOP_TIMEZONE`. The default timezone
is Asia/Singapore; set Asia/Ho_Chi_Minh for Vietnam shop-local reporting.

The model is instructed to ask for missing dates rather than choose a sales
period. Follow-up questions can use a date explicitly stated in the conversation.
The backend validates dates, limits ranges to 366 days, validates all tool calls
before executing any, and never executes generated SQL. Date interpretation and
clarification still depend on the model; the source panel shows the actual dates
queried so users can check them.

## How the files connect

`frontend/chat.html` sends a question and up to eight previous conversation
messages to `POST /api/assistant/chat`. `app.py` serves the page and registers
`assistant.py`'s router.

1. OpenRouter selects a predefined tool, with validated arguments. It can call
   `read_sales`, `read_stock`, `read_recipes`, or ask a clarification question.
2. `read_sales` reuses `analytics.dashboard` and its frozen approved sales prices
   and consumption movements. Only the requested view is returned to the model.
3. `read_stock` reuses the inventory repository's current-stock calculations.
   `read_recipes` uses predefined SQLAlchemy SELECT queries against the current
   catalog, recipe/price versions and preparation yields in a consistent snapshot.
4. The second OpenRouter request receives these results and explains them. The
   server returns both the explanation and source data to the page.

All totals and balances come from Python/SQL. Model prose is an interpretation,
not an independently verified accounting result. Use **Xem số liệu đối chiếu**
to check the returned figures and dates, or compare them with Dashboard/Inventory.
The assistant has no tools to change recipes, prices, inventory or sales.

## Quota, data and limitations

- At most two API calls per submitted question. A clarification uses one. Up to
  three validated data-tool calls may be requested in the first model response;
  these are local database reads, not additional model calls.
- No automatic client retry, unbounded model loop, or automatic paid fallback.
  OpenRouter itself may route across available free providers. One active chat
  request is allowed per app process to limit accidental duplicate submissions.
- The app does not claim to know your remaining account quota. Other uses of the
  same account and failed provider requests may affect it. On quota/network errors,
  the question stays in the input for a manual retry; Dashboard remains usable.
- Name searches ignore case and Vietnamese accents and disclose multiple matches.
  Results default to 20 rows, capped at 50; omitted rows are explicitly counted.
  Stock attention items come first. Search filters product/usage/activity rows,
  while sales summaries always describe the whole shop. Daily results include
  every date in the validated range. Oversized results request a narrower query.
- Sales are approved reports only. Missing reports do not imply zero sales.
  Unknown prices/balances stay unknown. Provisional prices/yields are flagged.
  Estimates are not actual receipt takings, profit, or stock forecasts.
- No chat database table: conversation history stays in page memory and disappears
  on refresh or “Cuộc trò chuyện mới”. Source figures are not resent as history;
  new questions fetch fresh data. Up to eight prior text messages are resent for
  conversational context.
- Questions and selected shop data are sent to OpenRouter and the selected model
  provider. API keys stay on the backend. The page renders model output as plain
  text, so model-generated HTML/scripts cannot execute.
- This remains the existing local prototype. It has no login or per-user access
  controls; authentication is still needed before public deployment.

## Verification

Provider responses are mocked in the test suite; tests do not consume Gemini or
OpenRouter quota. Unit checks cover key handling, free-only configuration, request
caps, history validation, malformed tools, unknown tools, failures and source data.
PostgreSQL integration checks use the separate test database to verify stock and
recipe reads, approved sales figures and no new inventory movements after chat.

```sh
python3 -m unittest discover -s tests -v
BOBAOPS_TEST_DATABASE=bobaops_test python3 -m unittest discover -s tests -v
```

The second command requires the separate test database from the database setup
guide. Actual free-model availability, Vietnamese answer quality and live latency
must be checked with a manual question; no live provider call is made by tests.

## Troubleshooting OpenRouter routing errors

A 404 saying no endpoint supports the request parameters can result from sending
an optional parameter that free providers do not advertise. The backend therefore
omits `parallel_tool_calls` and validates the maximum tool-call count itself.
`require_parameters=True` remains enabled so tools and tool choice must be
supported. The backend does not retry the request automatically.

Errors now include the OpenRouter HTTP status and distinguish unsupported
parameters, unavailable models and account privacy-policy restrictions. Provider
error bodies and API keys are not shown. Privacy settings are never changed by
the app. Routing behavior follows the
[provider-selection documentation](https://openrouter.ai/docs/guides/routing/provider-selection).

The backend also supplies server-calculated ISO dates for yesterday, the last
seven days, this week and this month to reduce model mistakes about the year.
