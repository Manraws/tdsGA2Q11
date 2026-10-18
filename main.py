import os
import base64
import json
import time
import uuid
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="Assignment API")

# Question 12: only the assigned origin plus an explicitly configured exam origin.
ASSIGNED_ORIGIN = "https://app-yrrxdi.example.com"
EXAM_ORIGIN = os.getenv("EXAM_ORIGIN", "").strip()
allowed_origins = [ASSIGNED_ORIGIN]
if EXAM_ORIGIN and EXAM_ORIGIN not in allowed_origins:
    allowed_origins.append(EXAM_ORIGIN)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID", "X-Client-Id", "Idempotency-Key"],
    expose_headers=["X-Request-ID", "Retry-After"],
)

EMAIL = "23f3003865@ds.study.iitm.ac.in"

# Rate-limit windows are independent by endpoint and X-Client-Id.
WINDOW_SECONDS = 10.0
PING_LIMIT = 9
ORDERS_LIMIT = 20
rate_lock = Lock()
request_times = defaultdict(deque)

# The in-memory data stores are suitable for a single long-running instance.
# They reset when the process restarts and are not shared between instances.
idempotency_lock = Lock()
idempotency = {}
catalog = [{"id": i, "name": f"Order {i}"} for i in range(1, 56)]


def _client_key(request: Request, scope: str) -> str:
    client_id = request.headers.get("X-Client-Id", "").strip()
    if not client_id:
        # Use the network peer as a fallback; never merge all anonymous clients.
        client_id = request.client.host if request.client else "unknown"
    return f"{scope}:{client_id}"


def _rate_limit(request: Request, scope: str, limit: int):
    # Preflight requests are handled by CORS and don't consume the application quota.
    if request.method == "OPTIONS":
        return None

    now = time.monotonic()
    key = _client_key(request, scope)
    with rate_lock:
        timestamps = request_times[key]
        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()
        if len(timestamps) >= limit:
            retry_after = max(1, int(WINDOW_SECONDS - (now - timestamps[0]) + 0.999))
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
        timestamps.append(now)
    return None


@app.middleware("http")
async def request_context(request: Request, call_next):
    # Request context is attached to every response, including 429 responses.
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    if request.url.path == "/ping":
        limited = _rate_limit(request, "ping", PING_LIMIT)
        if limited is not None:
            limited.headers["X-Request-ID"] = request_id
            return limited
    elif request.url.path == "/orders":
        limited = _rate_limit(request, "orders", ORDERS_LIMIT)
        if limited is not None:
            limited.headers["X-Request-ID"] = request_id
            return limited

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/ping")
async def ping(request: Request):
    return {"email": EMAIL, "request_id": request.state.request_id}


def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4))
        data = json.loads(raw.decode("utf-8"))
        offset = int(data["offset"])
        if offset < 0 or offset > len(catalog):
            raise ValueError
        return offset
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@app.post("/orders")
async def create_order(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    with idempotency_lock:
        if idempotency_key in idempotency:
            return idempotency[idempotency_key]
        result = {"id": str(uuid.uuid4())}
        idempotency[idempotency_key] = result
        return result


@app.get("/orders")
async def list_orders(
    limit: int = Query(default=10, ge=1, le=55),
    cursor: Optional[str] = Query(default=None),
):
    offset = _decode_cursor(cursor)
    items = catalog[offset:offset + limit]
    next_offset = offset + len(items)
    next_cursor = _encode_cursor(next_offset) if next_offset < len(catalog) else None
    return {
        "orders": items,
        "items": items,
        "next_cursor": next_cursor,
        "nextCursor": next_cursor,
    }


@app.get("/")
async def root():
    return {"status": "ok", "docs": "/docs"}
