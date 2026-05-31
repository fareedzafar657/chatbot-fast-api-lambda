from fastapi import APIRouter, Depends, Query
from app.middleware.auth import get_current_user
from app.models.schemas import (
    Message, PaginatedMessages, PatchMessageRequest
)
from app.services import db_messages as db

router = APIRouter(prefix="/messages", tags=["Messages"])


@router.get("/branch/{branch_id}", response_model=PaginatedMessages)
async def list_messages(
    branch_id: str,
    page_size: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    user: dict = Depends(get_current_user),
):
    """
    Get paginated messages for a branch in chronological order.

    Use include_deleted=true to show soft-deleted messages in the UI
    (e.g. for an admin or restore flow).

    The next_cursor in the response is passed back as cursor on the next call.
    has_more=false means you've reached the end.
    """
    return await db.list_messages(
        branch_id=branch_id,
        user_id=user["sub"],
        page_size=page_size,
        cursor=cursor,
        include_deleted=include_deleted,
    )


@router.patch("/{msg_id}", response_model=Message)
async def patch_message(
    msg_id: str,
    body: PatchMessageRequest,
    user: dict = Depends(get_current_user),
):
    """
    Update a message's state and/or content.

    Stop a message mid-stream:   { "state": "stopped" }
    Edit a completed message:    { "state": "edited", "content": "new text" }
    Soft-delete a message:       { "state": "deleted" }
    Restore a deleted message:   { "state": "active" }

    Note: editing content is only valid once the response is fully generated.
    The streaming Lambda enforces the stop state during generation.
    """
    return await db.patch_message(
        msg_id=msg_id,
        user_id=user["sub"],
        state=body.state,
        content=body.content,
    )
