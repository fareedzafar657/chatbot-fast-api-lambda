"""Session CRUD — list, get, update, delete."""
from boto3.dynamodb.conditions import Key
from fastapi import HTTPException
from app.services.db_base import (
    SESSIONS_TABLE, BRANCHES_TABLE, MESSAGES_TABLE,
    IDX_USER_UPDATED, IDX_SESSION_CREATED,
    get_table, now_iso, _paginate_query,
    encode_cursor, decode_cursor, db_to_session,
)


async def list_sessions(user_id: str, page_size: int, cursor: str | None) -> dict:
    table = get_table(SESSIONS_TABLE)

    kwargs = {
        "IndexName": IDX_USER_UPDATED,
        "KeyConditionExpression": Key("userId").eq(user_id),
        "Limit": page_size,
        "ScanIndexForward": False,   # most recent first
    }

    if cursor:
        kwargs["ExclusiveStartKey"] = decode_cursor(cursor)

    response = table.query(**kwargs)
    items = [db_to_session(i) for i in response.get("Items", [])]

    next_cursor = None
    if "LastEvaluatedKey" in response:
        next_cursor = encode_cursor(response["LastEvaluatedKey"])

    return {
        "items":       items,
        "count":       len(items),
        "page_size":   page_size,
        "has_more":    next_cursor is not None,
        "next_cursor": next_cursor,
    }


async def get_session(session_id: str, user_id: str) -> dict:
    table = get_table(SESSIONS_TABLE)
    response = table.get_item(Key={"sessionId": session_id})
    item = response.get("Item")

    if not item:
        raise HTTPException(status_code=404, detail="Session not found")
    if item.get("userId") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return db_to_session(item)


async def update_session(session_id: str, user_id: str, updates: dict) -> dict:
    await get_session(session_id, user_id)

    table = get_table(SESSIONS_TABLE)
    expressions = ["updatedAt = :now"]
    values = {":now": now_iso()}
    names = {}

    if "title" in updates and updates["title"] is not None:
        expressions.append("#title = :title")
        names["#title"] = "title"
        values[":title"] = updates["title"]

    if "active_branch_id" in updates and updates["active_branch_id"] is not None:
        expressions.append("activeBranchId = :abid")
        values[":abid"] = updates["active_branch_id"]

    kwargs = {
        "Key": {"sessionId": session_id},
        "UpdateExpression": "SET " + ", ".join(expressions),
        "ExpressionAttributeValues": values,
        "ReturnValues": "ALL_NEW",
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names

    response = table.update_item(**kwargs)
    return db_to_session(response["Attributes"])


async def delete_session(session_id: str, user_id: str):
    await get_session(session_id, user_id)

    # Delete the session row first — user no longer sees it even if child
    # deletion is interrupted by a timeout or throttle error
    get_table(SESSIONS_TABLE).delete_item(Key={"sessionId": session_id})

    messages_table = get_table(MESSAGES_TABLE)
    with messages_table.batch_writer() as batch:
        for item in _paginate_query(
            messages_table,
            IndexName=IDX_SESSION_CREATED,
            KeyConditionExpression=Key("sessionId").eq(session_id),
            ProjectionExpression="msgId",
        ):
            batch.delete_item(Key={"msgId": item["msgId"]})

    branches_table = get_table(BRANCHES_TABLE)
    with branches_table.batch_writer() as batch:
        for item in _paginate_query(
            branches_table,
            IndexName=IDX_SESSION_CREATED,
            KeyConditionExpression=Key("sessionId").eq(session_id),
            ProjectionExpression="branchId",
        ):
            batch.delete_item(Key={"branchId": item["branchId"]})
