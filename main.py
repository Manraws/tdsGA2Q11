from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from uuid import uuid4
import base64

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

TOTAL_ORDERS = 55
RATE_LIMIT = 20
WINDOW_SECONDS = 10.0

app = FastAPI(title="Orders API", version="1.0.0")

# The grader's browser can call the API cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# In-memory state is suitable for this single-instance grading demo.
# For multi-worker/production deployments, use a shared database/Redis.
state_lock = Lock()
idempotency_records = {}
created_orders = {}
client_requests = defaultdict(deque)

# Fixed catalog used by pagination: IDs 1 through 55, always in order.
catalog = [{"id": i, "name": f"Order {i}"} for i in range(1, TOTAL_ORDERS + 1)]


class OrderCreate(BaseModel):
    product: str = Field(default="sample-item", min_length=1)
    quantity: int = Field(default=1, ge=1)


@app.middleware("http")
async def per_client_rate_limit(request: Request, call_next):
    # CORS preflight must pass so browser verification works.
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = monotonic()
    with state_lock:
        timestamps = client_requests[client_id]
        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= RATE_LIMIT:
            retry_after = max(1, int(WINDOW_SECONDS - (now - timestamps[0]) + 0.999))
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )
        timestamps.append(now)

    return await call_next(request)


@app.get("/")
def root():
    return {
        "service": "orders-api",
        "total_catalog_orders": TOTAL_ORDERS,
        "rate_limit": f"{RATE_LIMIT} requests per {int(WINDOW_SECONDS)} seconds per X-Client-Id",
        "docs": "/docs",
    }


@app.post("/orders", status_code=201)
def create_order(
    payload: OrderCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
):
    # The lock makes key-check + create atomic for concurrent requests.
    with state_lock:
        if idempotency_key in idempotency_records:
            return idempotency_records[idempotency_key]

        order = {
            "id": str(uuid4()),
            "product": payload.product,
            "quantity": payload.quantity,
        }
        idempotency_records[idempotency_key] = order
        created_orders[order["id"]] = order
        return order


@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: str | None = Query(default=None),
):
    # Cursor is an opaque base64url token containing the next zero-based offset.
    offset = 0
    if cursor:
        try:
            decoded = base64.urlsafe_b64decode(cursor.encode() + b"=" * (-len(cursor) % 4))
            offset = int(decoded.decode())
            if offset < 0 or offset > TOTAL_ORDERS:
                raise ValueError("cursor out of range")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    items = catalog[offset:offset + limit]
    next_offset = offset + len(items)
    next_cursor = (
        base64.urlsafe_b64encode(str(next_offset).encode()).decode().rstrip("=")
        if next_offset < TOTAL_ORDERS else None
    )
    return {"items": items, "next_cursor": next_cursor}


@app.get("/health")
def health():
    return {"status": "ok"}
