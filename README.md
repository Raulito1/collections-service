# Collections Service (FastAPI)

Auth: Supabase (Google) JWT → verified via Supabase JWKS.  
Data: QuickBooks OAuth2 → fetch invoices → Postgres.  
Deploy: Render.

## Setup
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` → `.env` and fill values.
4. Run: `uvicorn app.main:app --reload --port 8000`

## Env
- `SUPABASE_URL` (e.g. https://<proj>.supabase.co)
- `SUPABASE_ANON_KEY` (frontend anon key)
- `SUPABASE_SERVICE_ROLE_KEY` (server-side key for Supabase client)
- `SUPABASE_JWKS_URL` (e.g. https://<proj>.supabase.co/auth/v1/.well-known/jwks.json)
- `SUPABASE_JWT_SECRET` (required if your Supabase tokens use HS256)
- `ALLOWED_ORIGINS` (comma list, e.g., http://localhost:5173,https://your-web.vercel.app)
- QBO_* (from Intuit)

## OAuth
- Start: `GET /auth/quickbooks/login`
- Callback: `GET /auth/quickbooks/callback`
- Sync: `POST /api/v1/quickbooks/sync`

## Health
- `GET /health` → `{ ok: true }`
