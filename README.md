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
