import os
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()

# Configure your email: either via environment variable LOGIN_EMAIL on Render
# or change the fallback string below to your actual student email.
EMAIL = os.getenv("LOGIN_EMAIL", "your-logged-in-email@example.com")


# -----------------------------------------
# Middleware 1: Request context propagation
# -----------------------------------------
class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode("latin-1").strip()

        if not request_id:
            request_id = str(uuid.uuid4())

        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append(
                    (b"x-request-id", request_id.encode("latin-1"))
                )
                message["headers"] = response_headers

            await send(message)

        await self.app(scope, receive, send_with_request_id)


# -----------------------------------------
# Middleware 2: Per-client rate limiting
# -----------------------------------------
class RateLimitMiddleware:
    def __init__(self, app, limit=9, window=10):
        self.app = app
        self.limit = limit
        self.window = window
        self.requests = defaultdict(deque)
        self.lock = Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Do not rate-limit preflight OPTIONS requests or other routes
        if scope.get("method") == "OPTIONS" or scope.get("path") != "/ping":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        client_id = headers.get(b"x-client-id", b"unknown").decode("latin-1").strip()
        if not client_id:
            client_id = "unknown"

        now = time.monotonic()

        with self.lock:
            bucket = self.requests[client_id]

            # Remove timestamps outside the 10-second window
            while bucket and now - bucket[0] >= self.window:
                bucket.popleft()

            if len(bucket) >= self.limit:
                limited = True
            else:
                bucket.append(now)
                limited = False

        if limited:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# -----------------------------------------
# Middleware Registration Order Matters!
# The LAST added middleware wraps all earlier ones.
# -----------------------------------------
# 1. Rate limiting (inner)
app.add_middleware(RateLimitMiddleware, limit=9, window=10)

# 2. Request context (wraps rate limiter so 429 also gets x-request-id)
app.add_middleware(RequestContextMiddleware)

# 3. CORS (outermost - added LAST so it wraps every response including 429s and handles preflights)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# -----------------------------------------
# Endpoints
# -----------------------------------------
@app.get("/ping")
async def ping(request: Request):
    return {
        "email": EMAIL,
        "request_id": request.scope["state"]["request_id"],
    }


@app.get("/")
async def root():
    return {"status": "ok"}
