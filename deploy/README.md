# Render Free + Supabase Free

Use one new Supabase project exclusively for this app. Your local Docker app stays
available at port 8006 while the online copy is set up. Do not paste credentials in
chat or commit private env files.

## 1. Create Supabase

1. Sign up at <https://supabase.com/dashboard> and choose the **Free** plan.
2. Create a project named `bobaops`, preferably in **Singapore**. Save the generated
   database password in your password manager.
3. In **Storage**, create a bucket named `receipts`. Keep **Public bucket OFF**.
   Allow PNG, JPEG and WebP, with a 10 MB file-size limit.
4. Under the project's Data API settings, disable the **Data API**. This app uses
   PostgreSQL directly and the separate Storage API; browser database access is unused.
5. Open **Connect → Session pooler** and copy the PostgreSQL URI on **port 5432**.
   Replace the password placeholder privately; URL-encode special characters.
   Use Session mode, not Transaction mode (6543).
6. Find the **Project URL** and a backend **secret key** (`sb_secret_...`) under the
   project's API key settings. A legacy `service_role` key also works. A publishable
   or `anon` key is not the backend secret key.

## 2. Fill private settings locally

The ignored `.env.render` file is separate from the existing `.env`. If it does not
exist yet, create it:

```sh
cp deploy/render.env.example .env.render
```

Open `.env.render` in your editor and fill:

```dotenv
DATABASE_URL=postgresql://postgres.PROJECT_REF:ENCODED_PASSWORD@POOLER_HOST:5432/postgres
POSTGRES_SSLMODE=require
SHOP_STORAGE=postgres
RECEIPT_STORAGE=supabase
SUPABASE_URL=https://PROJECT_REF.supabase.co
SUPABASE_SECRET_KEY=YOUR_BACKEND_SECRET_KEY
SUPABASE_STORAGE_BUCKET=receipts
SHOP_TIMEZONE=Asia/Ho_Chi_Minh
```

Use the actual host from Connect; do not construct it from the region name. Keep
the existing `.env` pointed to your Mac database. With your usual virtual environment
active, check the new settings:

```sh
python3 -m scripts.check_supabase --env-file .env.render
```

This checks the cloud database and the private bucket without writing any shop data.
Then stop uploads/edits briefly and copy the current shop records to the empty cloud
database:

```sh
python3 -m scripts.prepare_cloud --env-file .env.render --copy-shop-data
```

This applies Alembic migrations and copies current products, ingredients, recipes,
prices, purchases and stock events in a transaction. It preserves version history,
restricts anonymous Data API access and refuses to overwrite a populated target.
Local records are unchanged. Login sessions are not copied. Receipt photos must be
absent for this initial copy; the test receipts were cleared before deployment.
If you upload more locally first, pause here and transfer those photos deliberately.

For future schema releases, use the same command **without** `--copy-shop-data`.
Do not repeatedly copy local shop records after the online app has its own data.

## 3. Create Render

1. Sign up at <https://dashboard.render.com> using GitHub.
2. Commit and push these code changes, including `render.yaml`, to your repository.
   Keep `.env`, `.env.render` and `data/private/` out of Git.
3. Choose **New → Blueprint**, connect the repository and select `render.yaml`.
4. Confirm the web service uses **Free** and there is **no Render database or disk**.
   The database and photos are stored on Supabase.
5. Fill the secret values requested by the Blueprint:

| Setting | Where to get it |
| --- | --- |
| `DATABASE_URL` | Your private `.env.render` |
| `SUPABASE_URL` | Your private `.env.render` |
| `SUPABASE_SECRET_KEY` | Your private `.env.render` |
| `AUTH_USERNAME` | Your existing local `.env` |
| `AUTH_PASSWORD_HASH` | Your existing local `.env`, without surrounding quotes |
| `GEMINI_API_KEY` | Your existing local `.env` |
| `OPENROUTER_API_KEY` | Your existing local `.env` |

The Blueprint sets non-secret options and generates a new session secret. It uses
your existing username/password hash, so you can sign in with the same password.
It gets its HTTPS origin automatically from Render's `RENDER_EXTERNAL_URL`. With a
custom domain later, set `AUTH_ALLOWED_ORIGIN` to that exact HTTPS origin instead.
If you changed your Gemini model locally, enter the working model in Render too.

6. Deploy and wait for `/health/ready` to return `{"status":"ok"}`.
7. Open the assigned `https://...onrender.com` URL and sign in.

If Blueprint setup is unavailable, create a **Web Service** from the repository,
select **Docker**, **Free**, Singapore, port 8000, and health path `/health/ready`.
Use the same variables from `render.yaml`, with a random session secret of at least
32 characters. No migration start command is required: migrations ran from your Mac.

## 4. Verify online

Use a clearly marked test receipt: upload → review → approve → verify inventory and
dashboard → check chat. Verify a repeated upload is rejected. Restart/redeploy the
Render service and confirm the saved receipt photo still opens. Reverse the approved
test report to restore its deductions; keep that audit history or clear it deliberately
before beginning real usage. Check that an incognito window requires login.

Render Free sleeps when idle, so the next visit may be slow. Supabase Free may pause
inactive projects. Stay within both free plans' storage, compute and bandwidth quotas.
Database backups and bucket-photo backups are separate; keep both privately.

## How photos stay private

Only the backend uses the Supabase secret key. The database stores an object reference,
not a public URL. `/api/sales/images/...` authenticates the app session, downloads the
private object server-side and returns the image with `Cache-Control: no-store`.
Public buckets are rejected by readiness checks and uploads. Local Docker still uses
local photo files unless you explicitly select `RECEIPT_STORAGE=supabase`.

Sources: [Render Blueprints](https://render.com/docs/blueprint-spec),
[Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[Supabase storage access](https://supabase.com/docs/guides/storage/security/access-control).
