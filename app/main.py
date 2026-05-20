import logging
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.config import get_settings
from app.routers import sessions, branches, messages

settings = get_settings()
logger = logging.getLogger(__name__)

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Chatbot API",
    description="REST API for managing chat sessions, branches, and messages etc.",
    version="1.0.0",
)

# ─── CORS ────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
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
