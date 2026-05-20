from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user
from app.models.schemas import Branch, ForkBranchRequest, CherryPickRequest, CherryPickResponse, CompactRequest, CompactResponse, DeleteCompactionResponse, SuccessResponse
from app.services import dynamodb as db

router = APIRouter(prefix="/branches", tags=["Branches"])


@router.get("/session/{session_id}", response_model=list[Branch])
async def list_branches(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """
    List all branches for a session as a flat list.
    Use parentBranchId to reconstruct the tree on the frontend.
    """
    return await db.list_branches(session_id, user["sub"])


@router.get("/{branch_id}", response_model=Branch)
async def get_branch(
    branch_id: str,
    user: dict = Depends(get_current_user),
):
    """Get a single branch including its selectedMsgIds."""
    return await db.get_branch(branch_id, user["sub"])


@router.post("/fork", response_model=Branch, status_code=201)
async def fork_branch(
    body: ForkBranchRequest,
    user: dict = Depends(get_current_user),
):
    """
    Create a new branch from a custom selection of messages.

    The caller passes selectedMsgIds — the ordered list of messages
    that will be fed to Bedrock on the next prompt in this branch.
    The original branch is preserved intact.
    """
    return await db.fork_branch(
        user_id=user["sub"],
        data=body.model_dump(),
    )


@router.post("/{branch_id}/cherry-pick", response_model=CherryPickResponse, status_code=200)
async def cherry_pick(
    branch_id: str,
    body: CherryPickRequest,
    user: dict = Depends(get_current_user),
):
    """Append copied messages from other branches to an existing branch."""
    return await db.cherry_pick_messages(
        branch_id=branch_id,
        user_id=user["sub"],
        source_msg_ids=body.source_msg_ids,
    )


@router.post("/{branch_id}/compact", response_model=CompactResponse, status_code=200)
async def compact_branch(
    branch_id: str,
    body: CompactRequest,
    user: dict = Depends(get_current_user),
):
    """Replace selected messages with an AI-generated summary to free context tokens."""
    return await db.compact_messages(
        branch_id=branch_id,
        user_id=user["sub"],
        msg_ids=body.msg_ids,
        name=body.name,
        provider=body.provider,
        model=body.model,
        api_key=body.api_key,
    )


@router.delete("/{branch_id}/compact/{summary_msg_id}", response_model=DeleteCompactionResponse)
async def delete_compaction(
    branch_id: str,
    summary_msg_id: str,
    user: dict = Depends(get_current_user),
):
    """Revert a compaction: restore original messages to active and remove the summary."""
    return await db.delete_compaction(
        branch_id=branch_id,
        summary_msg_id=summary_msg_id,
        user_id=user["sub"],
    )
