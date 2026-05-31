"""FastAPI app wiring — routers, middleware, error handler, Lambda export.

Business logic lives in app/services/, schemas in app/models/, JWT auth in
app/middleware/. This file should never grow domain logic.

Local CORS is mounted only when running outside Lambda; on Lambda the
Function URL configuration handles CORS.
"""

import logging
import os
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.routers import sessions, branches, messages

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Chatbot API",
    description="REST API for managing chat sessions, branches, and messages etc.",
    version="1.0.0",
)

# ─── Local CORS ──────────────────────────────────────────────────────────────
# Only active when running locally — on Lambda the Function URL handles CORS.

if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ─── Request Logging ──────────────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start

    logger.info(
        f"{request.method} {request.url.path}",
        extra={
            "status_code": response.status_code,
            "duration_ms": round(duration * 1000),
            "client_ip": request.client.host if request.client else "unknown",
        }
    )
    return response

# ─── Routers ─────────────────────────────────────────────────────────────────

app.include_router(sessions.router)
app.include_router(branches.router)
app.include_router(messages.router)

# ─── Health check ────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ─── Global error handler ────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)

    logger.error(
        "Unhandled exception",
        exc_info=True,
        extra={
            "path": request.url.path,
            "method": request.method,
            "client": request.client.host if request.client else "unknown",
        }
    )

    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


# ─── Lambda handler ───────────────────────────────────────────────────────────

handler = Mangum(app, lifespan="off")
