from fastapi import APIRouter, Depends, Query
from app.middleware.auth import get_current_user
from app.models.schemas import (
    Session, PaginatedSessions, UpdateSessionRequest, SuccessResponse, UsageStats
)
from app.services import dynamodb as db

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.get("/usage/stats", response_model=UsageStats)
async def get_usage_stats(
    user: dict = Depends(get_current_user),
):
    """Return aggregated token usage for the current user."""
    return await db.get_usage_stats(user["sub"])


@router.get("", response_model=PaginatedSessions)
async def list_sessions(
    page_size: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, description="Pagination cursor from previous response"),
    user: dict = Depends(get_current_user),
):
    """List all sessions for the current user, most recently active first."""
    result = await db.list_sessions(
        user_id=user["sub"],
        page_size=page_size,
        cursor=cursor,
    )
    return result


@router.get("/{session_id}", response_model=Session)
async def get_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """Get a single session by ID."""
    return await db.get_session(session_id, user["sub"])


@router.patch("/{session_id}", response_model=Session)
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    user: dict = Depends(get_current_user),
):
    """Update session title or switch the active branch."""
    return await db.update_session(
        session_id,
        user["sub"],
        body.model_dump(exclude_none=True),
    )


@router.delete("/{session_id}", response_model=SuccessResponse)
async def delete_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete a session and all its branches and messages."""
    await db.delete_session(session_id, user["sub"])
    return SuccessResponse(message="Session deleted")
