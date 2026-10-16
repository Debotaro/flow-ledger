"""Authenticated, bounded same-origin API for a single local operations workspace."""

import hmac
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .models import Attribution, Decision, EventInput, Status
from .service import seed, tick
from .store import Conflict, Store, utcnow


class BodyLimit:
    def __init__(self, app, limit=64_000):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.limit:
                        return await JSONResponse({"detail": "Request body exceeds 64 KB."}, 413)(scope, receive, send)
                except ValueError:
                    return await JSONResponse({"detail": "Invalid Content-Length."}, 400)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                return await JSONResponse({"detail": "Request body exceeds 64 KB."}, 413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, buffered_receive, send)


def create_app(settings: Settings | None = None, clock=utcnow):
    settings = settings or Settings()
    store = Store(settings, clock)
    app = FastAPI(title="FlowLedger", version="1.0.0", docs_url=None, redoc_url=None)
    app.state.store = store
    app.add_middleware(BodyLimit)

    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.hosts))

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if not settings.token and request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            return JSONResponse({"detail": "Remote access requires FLOW_API_TOKEN."}, 403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    allowed = (parsed.scheme == request.url.scheme and parsed.netloc == request.headers.get("host")
                               and not parsed.path and not parsed.query and not parsed.fragment)
                except ValueError:
                    allowed = False
                if not allowed:
                    return JSONResponse({"detail": "Cross-origin writes are not allowed."}, 403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def authorize(request: Request):
        if settings.token:
            value = request.headers.get("authorization", "")
            if not hmac.compare_digest(value.encode(), f"Bearer {settings.token}".encode()):
                raise HTTPException(401, "Enter the workspace API token to continue.")

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Job not found."}, 404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_unavailable(request, exc):
        return JSONResponse({"detail": "Storage is temporarily unavailable. Retry shortly."}, 503,
                            headers={"Retry-After": "1"})

    protected = [Depends(authorize)]

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "1.0.0", "auth_required": bool(settings.token),
                "demo_mode": settings.demo_mode, "lease_seconds": settings.lease_seconds, "max_attempts": settings.max_attempts}

    @app.get("/api/overview", dependencies=protected)
    def overview():
        return store.overview()

    @app.get("/api/jobs", dependencies=protected)
    def jobs(status: Status | None = None):
        return {"jobs": store.jobs(status)}

    @app.get("/api/jobs/{job_id}", dependencies=protected)
    def detail(job_id: str):
        return store.detail(job_id)

    @app.post("/api/events", dependencies=protected)
    def intake(event: EventInput, idempotency_key: str = Header(..., min_length=3, max_length=128)):
        return store.intake(idempotency_key, event)

    @app.post("/api/jobs/{job_id}/decision", dependencies=protected)
    def decision(job_id: str, body: Decision):
        return {"job": store.decide(job_id, body.decision, body.actor, body.note)}

    @app.post("/api/jobs/{job_id}/replay", dependencies=protected)
    def replay(job_id: str, body: Attribution):
        return {"job": store.replay(job_id, body.actor, body.note)}

    @app.post("/api/worker/tick", dependencies=protected)
    def worker_tick():
        return tick(store)

    @app.get("/api/cases", dependencies=protected)
    def cases():
        return {"cases": store.cases()}

    @app.get("/api/audit", dependencies=protected)
    def audit():
        return {"events": store.audit()}

    @app.post("/api/seed", dependencies=protected)
    def seed_events():
        return {"added": seed(store)}

    @app.get("/api/verification", dependencies=protected)
    def verification():
        if not settings.report.exists():
            return {"status": "not_run"}
        return json.loads(settings.report.read_text(encoding="utf-8"))

    static = Path(__file__).parent / "static"
    app.mount("/assets", StaticFiles(directory=static, check_dir=False), name="assets")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
