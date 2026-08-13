# CAssist

CAssist extracts fields, tables, and text from accounting documents for review and Tally JSON
export. Extraction is document-led: it does not require templates or invent missing values.

A classification step rejects unrelated files and asks for confirmation when a document is
ambiguous. CAssist accepts up to 10 PDF, JPEG, or PNG files per upload, with a 25 MiB limit per file.

## Stack

```text
frontend/       Vite, React 19, Tailwind CSS 4, shadcn/ui
backend/        FastAPI, PostgreSQL, Strands Agents, Cloudflare R2
ARCHITECTURE.md Data model, API contract, and security design
compose.yaml    Local PostgreSQL service
```

## Run locally

Requirements: Docker, `uv`, nvm, and `pnpm`. The project pins Node 24 in `.nvmrc` and Python 3.12 in
`backend/.python-version`.

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

3. Start the single-concurrency worker in another terminal:

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

Open `http://localhost:5173`. API docs are at `http://localhost:8000/docs`.

The dashboard handles upload, search, filtering, processing, review, export, cancellation, and
deletion. The shared document bucket has an application-enforced 8 GB limit. Development builds
also expose model comparison at `/dev/compare/:documentId`.

## Authentication

CAssist uses an Auth0 Regular Web Application with Google OAuth. Enable only the `google-oauth2`
connection and configure:

```text
Allowed Callback URL: http://localhost:8000/api/v1/auth/callback
Allowed Logout URL:   http://localhost:5173
```

Set `AUTH_ISSUER_URL`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, and a random 32-character-or-longer
`AUTH_STATE_SECRET` in `backend/.env`. The backend also restricts access to its configured Google
account allowlist.

Sessions idle after 12 hours, expire after 14 days, and allow up to 10 signed-in devices per account.
The frontend uses `http://localhost:8000/api/v1` by default; set `VITE_API_BASE_URL` in
`frontend/.env` to override it.

## Object storage

Create a private Cloudflare R2 bucket and an Object Read & Write token scoped to that bucket. Set
`R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET_NAME` in
`backend/.env`.

Keep public access disabled. For local development, allow CORS from `http://localhost:5173` for
`PUT`, `GET`, and `HEAD`, including the `Content-Type` request header.

## Verification

Run the synthetic model smoke test:

```bash
cd backend
uv run --locked python scripts/live_model_smoke.py
```

After the normal test suites pass, run the complete synthetic workflow:

```bash
cd backend
uv run --locked python scripts/local_vertical_smoke.py
```

Both scripts refuse to run in production. The vertical smoke test exercises upload, private R2,
extraction, review, correction, approval, export, viewing, and deletion, then removes its test data.

## Private deployment

Production serves the frontend and proxied API from `https://cassist.pages.dev`. Configure these
Cloudflare Pages Function bindings:

```text
CASSIST_ORIGIN=https://replace-with-nas-name.tailnet-name.ts.net
CASSIST_PROXY_SECRET=replace-with-at-least-32-random-characters
```

Use the same secret as `EDGE_PROXY_SECRET` on the NAS. Leave `VITE_API_BASE_URL` unset for the
production build so requests use same-origin `/api/v1`.

Validate the production Compose configuration from the repository root:

```bash
docker compose --env-file deploy/.env.production \
  -f deploy/compose.production.yaml config --quiet
```

Build and deploy Cloudflare Pages from `frontend/`:

```bash
pnpm build
pnpm exec wrangler pages deploy dist --project-name cassist --branch main
```

See [`deploy/README.md`](deploy/README.md) for NAS deployment, encrypted backups, retention, and
restore testing. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full rollout and security design.
