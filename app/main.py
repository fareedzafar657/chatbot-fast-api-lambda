from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.config import get_settings
from app.routers import sessions, branches, messages

settings = get_settings()

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Chatbot Management API",
    description="REST API for managing chat sessions, branches, and messages.",
    version="1.0.0",
    # Disable docs in production if needed:
    # docs_url=None, redoc_url=None
)

# ─── CORS ────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

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
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


# ─── Lambda handler ───────────────────────────────────────────────────────────
# Mangum wraps FastAPI to work as a Lambda Function URL handler.
# IMPORTANT: Function URL must be in BUFFERED mode (not RESPONSE_STREAM)
# since this is a regular REST API, not a streaming endpoint.

handler = Mangum(app, lifespan="off")
