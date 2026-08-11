# CAssist

CAssist is a side-tool for Chartered Accountants to upload accounting documents,
extract structured information, review it, and export it for downstream software.

## Structure

```text
frontend/       Vite, React JavaScript, Tailwind CSS v4, shadcn/ui
backend/        FastAPI, PostgreSQL, Strands Agents, Cloudflare R2 integration
ARCHITECTURE.md Data model and REST API contract
compose.yaml    Local PostgreSQL service
```

## Run locally

1. Start PostgreSQL:

   ```bash
   docker compose up -d postgres
   ```

2. Start the API:

   ```bash
   cd backend
   cp .env.example .env
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e '.[dev]'
   alembic upgrade head
   uvicorn app.main:app --reload
   ```

3. Start the frontend in another terminal:

   ```bash
   cd frontend
   pnpm install
   pnpm dev
   ```

Open `http://localhost:5173`. API documentation is at
`http://localhost:8000/docs`.

## Configure authentication

CAssist uses Auth0 Universal Login through the FastAPI backend. Create an Auth0
Regular Web Application and configure these development URLs:

```text
Allowed Callback URL: http://localhost:8000/api/v1/auth/callback
Allowed Logout URL:   http://localhost:5173
```

Set `AUTH_ISSUER_URL`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, and a random
`AUTH_STATE_SECRET` of at least 32 characters in `backend/.env`. The Vite app
uses `http://localhost:8000/api/v1` by default; override it with
`VITE_API_BASE_URL` in `frontend/.env` when needed. Credentials and tokens must
never be committed.
