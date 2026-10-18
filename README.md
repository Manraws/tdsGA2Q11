# FastAPI Orders API

Implements:
- `POST /orders` with required `Idempotency-Key` header.
- `GET /orders?limit=10&cursor=...` with opaque cursor pagination over catalog IDs 1–55.
- Per-`X-Client-Id` sliding-window limit: 20 requests per 10 seconds; 21st request returns 429 and `Retry-After`.
- CORS enabled for browser-based cross-origin checks.

## Deploy on Render

1. Create a GitHub repository and upload `main.py`, `requirements.txt`, and `render.yaml`.
2. In Render, choose **New + → Blueprint** and connect that repository.
3. Deploy the `orders-api` service. Render will provide a public URL such as `https://orders-api-xxxx.onrender.com`.
4. Use that exact URL as the deployed API base URL. It cannot be known until Render creates the service.

## Quick local test

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
uvicorn main:app --reload
```

Create an order (repeat with the same key to verify the same ID):

```bash
curl -i -X POST http://127.0.0.1:8000/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-123' \
  -H 'X-Client-Id: tester' \
  -d '{"product":"book","quantity":1}'
```

Fetch a page:

```bash
curl 'http://127.0.0.1:8000/orders?limit=10' -H 'X-Client-Id: page-tester'
```

The in-memory state is designed for a single-instance grading demo. For production multi-instance deployments, move idempotency records and rate-limit buckets to a shared database/Redis store.
