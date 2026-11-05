# Local development and verification

## Run locally

Requirements: Python 3.14, an activated Python virtual environment, and Docker with Compose. Run commands from the project folder.

### 1. Install and configure

```sh
python3 -m pip install -r requirements.lock
test -f .env || cp .env.example .env
```

Edit `.env` using `.env.example` as a reference. Set `GEMINI_API_KEY` for extraction and `OPENROUTER_API_KEY` for chat. Use `SHOP_STORAGE=postgres` and `RECEIPT_STORAGE=local` for local development.

`requirements.lock` pins the deployment dependencies; `requirements.txt` records the accepted dependency ranges. API access is subject to each provider’s quotas and account settings.

### 2. Start PostgreSQL and configure login

Compose uses the local image tag `mixue_agent_postgres:18`. If that tag is missing, create it from the official PostgreSQL image:

```sh
docker pull postgres:18
docker tag postgres:18 mixue_agent_postgres:18
docker compose up -d db
python3 -m alembic upgrade head
mkdir -p data/private/sales_images
python3 -m scripts.setup_login
```

The database is available on `127.0.0.1:5433`, keeping port 5432 available for other projects. The login command privately prompts for a username and password and saves a password hash and session secret in `.env`.

### 3. Start the app

```sh
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open [the local app](http://127.0.0.1:8000), sign in, and use **Danh mục & công thức** to configure your catalog, ingredients and preparation recipes. Private shop data is not included in the public repository. An existing private `data/private/shop_config.json` can be imported into an empty migrated database with `python3 -m scripts.import_shop_config`.

| Page | Path |
| --- | --- |
| Tổng quan — dashboard | `/` |
| Nhập doanh số — upload and extraction | `/upload` |
| Hoá đơn đã lưu — saved sales | `/sales` |
| Cập nhật tồn kho — inventory updates | `/inventory` |
| Danh mục & công thức — catalog and recipes | `/setup` |
| Trợ lý AI — assistant | `/chat` |

The optional `app` Compose profile runs the app itself in Docker at `http://127.0.0.1:8006`. Its receipt photos live in a named volume, separate from the Python server’s local photo folder; copy existing photos when switching between those setups.

## Tests

Run the unit checks without provider calls:

```sh
python3 -m unittest discover -s tests -v
```

PostgreSQL integration tests are opt-in. Create the separate test database once if it does not already exist:

```sh
docker compose exec db createdb -U bobaops bobaops_test
BOBAOPS_TEST_DATABASE=bobaops_test python3 -m unittest discover -s tests -v
```

Integration tests create isolated schemas in a database ending in `_test`. They cover receipt persistence, duplicate handling, approval and reversal, inventory transactions, concurrent retries, private photo storage and cloud data copying. AI and cloud-storage responses are mocked in the tests, so they do not consume model quota or require real cloud credentials.

GitHub Actions runs the suite against PostgreSQL and builds the Docker image.


## Project layout

```text
app.py                Uvicorn entry point
bobaops/
  auth/               Login, sessions and CSRF protection
  catalog/            Products, stock items, recipes and versioned configuration
  extraction/         Gemini extraction prompt, schema and API
  sales/              Drafts, matching, approval and reversal
  inventory/          Stock events, calculations and actual powder usage
  analytics/          Dashboard queries
  assistant/          Read-only assistant and controlled tools
  core/               Database settings, paths, health checks and photo storage
frontend/             HTML, CSS, JavaScript and bundled Chart.js
migrations/           Alembic database history
scripts/              Administrative commands
tests/               Unit and integration tests
deploy/               Render/Supabase setup guide and env template
render.yaml           Render service configuration
Dockerfile            Deployment image
compose.yaml          Local app, PostgreSQL and optional pgAdmin
```

For the online deployment, see [Render + Supabase setup](README.md).
