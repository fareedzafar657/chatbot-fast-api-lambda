"""Branch CRUD, fork, and cherry-pick."""
from datetime import datetime, timezone
from uuid import uuid4
from boto3.dynamodb.conditions import Key
from fastapi import HTTPException, status
from app.services.db_base import (
    BRANCHES_TABLE, MESSAGES_TABLE,
    IDX_SESSION_CREATED,
    get_table, now_iso,
    _batch_get_messages, db_to_branch, db_to_message,
)
from app.services.db_sessions import get_session


async def list_branches(session_id: str, user_id: str) -> list[dict]:
    # Verify session ownership
    await get_session(session_id, user_id)

    table = get_table(BRANCHES_TABLE)
    response = table.query(
        IndexName=IDX_SESSION_CREATED,
        KeyConditionExpression=Key("sessionId").eq(session_id),
        ScanIndexForward=True,
    )
    return [db_to_branch(i) for i in response.get("Items", [])]


async def get_branch(branch_id: str, user_id: str) -> dict:
    table = get_table(BRANCHES_TABLE)
    response = table.get_item(Key={"branchId": branch_id})
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Branch not found")
    await get_session(item["sessionId"], user_id)
    return db_to_branch(item)


def _duplicate_messages(
    msg_ids: list[str],
    target_branch_id: str,
    user_id: str,
    *,
    restore_compacted: bool,
    verify_ownership: bool,
) -> list[dict]:
    """
    Batch-copy messages into target_branch_id with new msgIds and createdAt.
    Token counts are stripped (duplicates didn't consume new tokens).

    restore_compacted=True: copies of state='compacted' become state='active'
    and shed their compactedBy (used by fork — new branch starts fresh).

    verify_ownership=True: every source message's userId must match user_id
    (used by cherry-pick — caller has not pre-verified source ownership).

    Raises 400 if any msg_id is missing, 403 on ownership violation.
    """
    if not msg_ids:
        return []

    messages_table = get_table(MESSAGES_TABLE)
    fetched = _batch_get_messages(msg_ids)

    missing = [mid for mid in msg_ids if mid not in fetched]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Messages not found: {missing[:5]}",
        )

    if verify_ownership and any(m.get("userId") != user_id for m in fetched.values()):
        raise HTTPException(status_code=403, detail="Access denied to source message")

    now = now_iso()
    duplicated_items = []
    with messages_table.batch_writer() as batch:
        for msg_id in msg_ids:
            original = fetched[msg_id]
            duplicated = {
                **original,
                "msgId":     f"msg_{uuid4()}",
                "branchId":  target_branch_id,
                "createdAt": now,
            }
            duplicated.pop("inputTokens",  None)
            duplicated.pop("outputTokens", None)
            if restore_compacted and original.get("state") == "compacted":
                duplicated["state"] = "active"
                duplicated.pop("compactedBy", None)
            # Clear originalMsgIds on duplicated summaries — the IDs belong to the source
            # branch and must not be restored if this copy's compaction is deleted.
            if original.get("type") == "compaction-summary":
                duplicated["originalMsgIds"] = []
            batch.put_item(Item=duplicated)
            duplicated_items.append(duplicated)

    return duplicated_items


async def fork_branch(user_id: str, data: dict) -> dict:
    await get_session(data["session_id"], user_id)

    selected_msg_ids = data.get("selected_msg_ids", [])
    if selected_msg_ids:
        from app.services.db_messages import get_message
        # First message should always be from user when creating a branch
        first_msg = await get_message(selected_msg_ids[0], user_id)
        if first_msg["role"] != "user" and first_msg.get("type") != "compaction-summary":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="First message in branch must be from user"
            )

    branch_id = f"branch_{uuid4()}"

    _duplicate_messages(
        selected_msg_ids,
        branch_id,
        user_id,
        restore_compacted=True,
        verify_ownership=False,   # session ownership already verified above
    )

    item = {
        "branchId":       branch_id,
        "sessionId":      data["session_id"],
        "parentBranchId": data["parent_branch_id"],
        "parentMsgId":    data.get("parent_msg_id"),
        "label":          data.get("label") or f"fork-{int(datetime.now(timezone.utc).timestamp())}",
        "createdAt":      now_iso(),
    }

    table = get_table(BRANCHES_TABLE)
    table.put_item(Item=item)
    return db_to_branch(item)


async def cherry_pick_messages(branch_id: str, user_id: str, source_msg_ids: list[str]) -> dict:
    target_branch = await get_branch(branch_id, user_id)

    duplicated_items = _duplicate_messages(
        source_msg_ids,
        branch_id,
        user_id,
        restore_compacted=False,
        verify_ownership=True,
    )

    return {
        "branch":       target_branch,
        "new_messages": [db_to_message(item) for item in duplicated_items],
    }
