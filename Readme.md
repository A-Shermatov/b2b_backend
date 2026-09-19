# BrandMarket Backend

FastAPI + asyncpg + PostgreSQL B2B marketplace API (modular monolith, пакет `app/`).

## Quick start

```bash
cd backend
cp .env.example .env
docker compose up -d
pip install -e ".[dev]"
python scripts/seed.py
uvicorn app.main:app --reload --port 8000
```

> **Note:** Docker Postgres uses host port **5433** (not 5432) to avoid conflict with a local PostgreSQL installation on Windows.

API docs: http://localhost:8000/docs

## File storage

Uploads go to S3-compatible storage (MinIO locally, Cloudflare R2 or AWS S3 in production). The API stores the **object key** and returns a public URL built from `FILES_BASE_URL`.

| Environment | `S3_ENDPOINT_URL` (backend SDK) | `FILES_BASE_URL` (browser) |
|-------------|----------------------------------|----------------------------|
| Local uvicorn + MinIO | `http://localhost:9000` | `http://localhost:9000/brandmarket` |
| Docker Compose | `http://minio:9000` | `http://localhost:9000/brandmarket` |
| Render + Cloudflare R2 | `https://<accountid>.r2.cloudflarestorage.com` | `https://pub-XXXX.r2.dev` or a custom domain |
| AWS S3 / CloudFront | AWS default | bucket website URL or CloudFront |

Render disk is ephemeral — do not rely on `/uploads` there. Set `S3_*` and `FILES_BASE_URL` on the Render service.

Frontend: `NEXT_PUBLIC_FILES_BASE_URL` (same public prefix) for relative/legacy paths. Absolute `https://` URLs from the API are used as-is.

## API prefixes

| Prefix | Audience |
|--------|----------|
| `/api/v1/auth` | Registration, login, profile |
| `/api/v1/public` | Categories, supplier directory, public reviews |
| `/api/v1/buyer` | Buyer RFQs, proposals, contracts, payments |
| `/api/v1/supplier` | Supplier board, proposals, contracts |
| `/api/v1/admin` | User/company moderation, disputes |

## Demo accounts (after seed)

| Role | Email | Password |
|------|-------|----------|
| Admin | admin@example.com | Admin123! |
| Buyer | buyer@example.com | Buyer123! |
| Supplier | supplier@example.com | Supplier123! |

## Headers

- `Authorization: Bearer <access_token>`
- `X-Actor-Id: <active_actor_id>`
- `X-Company-Id: <active_company_id>`

## Tests

```bash
python -m pytest
```
