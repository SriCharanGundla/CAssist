# CAssist

CAssist is a side-tool for Chartered Accountants to upload accounting documents and images, extract
the labels, values, tables, and text actually present, review or copy them, and export reviewed data.
Extraction is document-led: it does not require an invoice template or invent missing fields.

## Structure

```text
frontend/       Vite, React JavaScript, Tailwind CSS v4, shadcn/ui
backend/        FastAPI, PostgreSQL, Strands Agents, Cloudflare R2 integration
ARCHITECTURE.md Data model and REST API contract
compose.yaml    Local PostgreSQL service
```

## Run locally

Prerequisites: Docker, `uv`, nvm, and `pnpm`. Node 24 LTS is pinned in `.nvmrc`; run `nvm use` before
frontend commands. The backend Python version and environment are managed by `uv`; do not create or
activate a virtual environment manually.

1. Start PostgreSQL:

   ```bash
   docker compose up -d postgres
   ```

2. Start the API:

   ```bash
   cd backend
   cp .env.example .env
   uv sync
   uv run --locked alembic upgrade head
   uv run --locked uvicorn app.main:app --reload
   ```

3. Start the single-concurrency extraction worker in another terminal:

   ```bash
   cd backend
   uv run --locked cassist-worker
   ```

4. Start the frontend in another terminal:

   ```bash
   cd frontend
   nvm use
   pnpm install
   pnpm dev
   ```

Open `http://localhost:5173`. API documentation is at
`http://localhost:8000/docs`.

The dashboard is the document hub: it shows processing state and provides original viewing, review,
development model comparison, and deletion actions without an intermediate document page. Review
uses an embedded PDF.js viewer for PDFs. Account and workspace information is available at
`/settings`; `/dev/compare/:documentId` is available only in development builds.

## Configure authentication

CAssist uses Auth0 through the FastAPI backend and forces Google OAuth. In the Auth0 Regular Web
Application, enable the `google-oauth2` social connection and disable the database, passwordless,
and every other social connection for this application. Configure these development URLs:

```text
Allowed Callback URL: http://localhost:8000/api/v1/auth/callback
Allowed Logout URL:   http://localhost:5173
```

Set `AUTH_ISSUER_URL`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, and a random
`AUTH_STATE_SECRET` of at least 32 characters in `backend/.env`. The Vite app
uses `http://localhost:8000/api/v1` by default; override it with
`VITE_API_BASE_URL` in `frontend/.env` when needed. Credentials and tokens must
never be committed.

FastAPI independently accepts only the verified Google accounts `owner@example.test` and
`reviewer@example.test`. This server-side allowlist also blocks or revokes sessions even if another
Auth0 connection is accidentally enabled later.

To verify the development model adapter with generated synthetic data only:

```bash
cd backend
uv run --locked python scripts/live_model_smoke.py
```

The script creates its financial-document image inside a temporary directory, never uploads it to R2 or stores
it in PostgreSQL, prints no extracted financial values or provider output, and deletes the image on
exit. It refuses to run when `APP_ENV=production`.

After the ordinary test suites pass, verify the complete local workflow with synthetic data:

```bash
cd backend
uv run --locked python scripts/local_vertical_smoke.py
```

This gate refuses production and refuses to run while another processing job is active. It creates a
temporary synthetic user/workspace, exercises authenticated upload, private R2, the real development
model, review, correction, approval, export, original viewing, and permanent deletion, then removes
its synthetic database and R2 data.

## Configure development object storage

Create a private Cloudflare R2 bucket and an Object Read & Write API token scoped only to that
bucket. Set `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET_NAME` in
`backend/.env`. The bucket must keep public access disabled and allow CORS only from
`http://localhost:5173` for `PUT`, `GET`, and `HEAD` with the `Content-Type` request header.
