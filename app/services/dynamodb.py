import boto3
import base64
import json
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import HTTPException, status
from app.config import get_settings

settings = get_settings()

# ─── Client (singleton) ───────────────────────────────────────────────────────
_dynamodb = None

def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
    return _dynamodb


def get_table(name: str):
    return get_dynamodb().Table(name)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode_cursor(last_evaluated_key: dict) -> str:
    return base64.b64encode(json.dumps(last_evaluated_key).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    try:
        return json.loads(base64.b64decode(cursor.encode()).decode())
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor"
        )


def db_to_message(item: dict) -> dict:
    """Convert DynamoDB item (camelCase) → API response (snake_case)."""
    return {
        "msg_id":        item.get("msgId"),
        "session_id":    item.get("sessionId"),
        "branch_id":     item.get("branchId"),
        "role":          item.get("role"),
        "content":       item.get("content"),
        "state":         item.get("state", "active"),
        "user_id":       item.get("userId", ""),
        "parent_msg_id": item.get("parentMsgId"),
        "input_tokens":  int(item["inputTokens"])  if item.get("inputTokens")  else None,
        "output_tokens": int(item["outputTokens"]) if item.get("outputTokens") else None,
        "created_at":    item.get("createdAt"),
        "updated_at":    item.get("updatedAt"),
    }


def db_to_branch(item: dict) -> dict:
    return {
        "branch_id":        item.get("branchId"),
        "session_id":       item.get("sessionId"),
        "parent_branch_id": item.get("parentBranchId"),
        "parent_msg_id":    item.get("parentMsgId"),
        "selected_msg_ids": item.get("selectedMsgIds", []),
        "label":            item.get("label", ""),
        "created_at":       item.get("createdAt"),
    }


def db_to_session(item: dict) -> dict:
    return {
        "session_id":       item.get("sessionId"),
        "user_id":          item.get("userId"),
        "trunk_branch_id":  item.get("trunkBranchId"),
        "active_branch_id": item.get("activeBranchId"),
        "title":            item.get("title"),
        "created_at":       item.get("createdAt"),
        "updated_at":       item.get("updatedAt"),
    }


# ─── Sessions ────────────────────────────────────────────────────────────────

async def list_sessions(user_id: str, page_size: int, cursor: str | None) -> dict:
    table = get_table(settings.dynamo_sessions_table)

    kwargs = {
        "IndexName": "userId-updatedAt-index",
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
        "total":       len(items),
        "page_size":   page_size,
        "has_more":    next_cursor is not None,
        "next_cursor": next_cursor,
        "page":        1,
    }


async def get_session(session_id: str, user_id: str) -> dict:
    table = get_table(settings.dynamo_sessions_table)
    response = table.get_item(Key={"sessionId": session_id})
    item = response.get("Item")

    if not item:
        raise HTTPException(status_code=404, detail="Session not found")
    if item.get("userId") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return db_to_session(item)


async def update_session(session_id: str, user_id: str, updates: dict) -> dict:
    # Verify ownership first
    await get_session(session_id, user_id)

    table = get_table(settings.dynamo_sessions_table)
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
    table = get_table(settings.dynamo_sessions_table)
    table.delete_item(Key={"sessionId": session_id})


# ─── Branches ────────────────────────────────────────────────────────────────

async def list_branches(session_id: str, user_id: str) -> list[dict]:
    # Verify session ownership
    await get_session(session_id, user_id)

    table = get_table(settings.dynamo_branches_table)
    response = table.query(
        IndexName="sessionId-createdAt-index",
        KeyConditionExpression=Key("sessionId").eq(session_id),
        ScanIndexForward=True,
    )
    return [db_to_branch(i) for i in response.get("Items", [])]


async def get_branch(branch_id: str) -> dict:
    table = get_table(settings.dynamo_branches_table)
    response = table.get_item(Key={"branchId": branch_id})
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Branch not found")
    return db_to_branch(item)


async def fork_branch(user_id: str, data: dict) -> dict:
    # Verify session ownership
    await get_session(data["session_id"], user_id)

    # Validate: first selected message must be from user
    selected_msg_ids = data.get("selected_msg_ids", [])
    if selected_msg_ids:
        first_msg_id = selected_msg_ids[0]
        first_msg = await get_message(first_msg_id, user_id)
        if first_msg["role"] != "user":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="First message in branch must be from user"
            )

    branch_id = f"branch_{uuid4()}"

    # Duplicate selected messages into the new branch with new IDs
    messages_table = get_table(settings.dynamo_messages_table)
    new_msg_ids = []
    for msg_id in selected_msg_ids:
        try:
            response = messages_table.get_item(Key={"msgId": msg_id})
            if "Item" in response:
                original_msg = response["Item"]
                new_msg_id = f"msg_{uuid4()}"
                # Create a copy with new msgId and new branchId
                duplicated_msg = {
                    **original_msg,
                    "msgId": new_msg_id,
                    "branchId": branch_id,
                    "createdAt": now_iso(),
                }
                messages_table.put_item(Item=duplicated_msg)
                new_msg_ids.append(new_msg_id)
        except ClientError:
            pass  # skip if message not found

    # Create the branch with the new message IDs
    item = {
        "branchId":       branch_id,
        "sessionId":      data["session_id"],
        "parentBranchId": data["parent_branch_id"],
        "parentMsgId":    data.get("parent_msg_id"),
        "selectedMsgIds": new_msg_ids,
        "label":          data.get("label") or f"fork-{int(datetime.now().timestamp())}",
        "createdAt":      now_iso(),
    }

    table = get_table(settings.dynamo_branches_table)
    table.put_item(Item=item)
    return db_to_branch(item)


# ─── Messages ────────────────────────────────────────────────────────────────

async def list_messages(
    branch_id: str,
    user_id: str,
    page_size: int,
    cursor: str | None,
    include_deleted: bool = False,
) -> dict:
    # Get branch to verify it belongs to a session owned by this user
    branch = await get_branch(branch_id)
    await get_session(branch["session_id"], user_id)

    table = get_table(settings.dynamo_messages_table)

    kwargs = {
        "IndexName": "branchId-createdAt-index",
        "KeyConditionExpression": Key("branchId").eq(branch_id),
        "Limit": page_size,
        "ScanIndexForward": True,    # oldest first (chronological)
    }

    if not include_deleted:
        kwargs["FilterExpression"] = "attribute_not_exists(#st) OR #st <> :deleted"
        kwargs["ExpressionAttributeNames"] = {"#st": "state"}
        kwargs["ExpressionAttributeValues"] = {":deleted": "deleted"}

    if cursor:
        kwargs["ExclusiveStartKey"] = decode_cursor(cursor)

    response = table.query(**kwargs)
    items = [db_to_message(i) for i in response.get("Items", [])]

    next_cursor = None
    if "LastEvaluatedKey" in response:
        next_cursor = encode_cursor(response["LastEvaluatedKey"])

    return {
        "items":       items,
        "total":       len(items),
        "page_size":   page_size,
        "page":        1,
        "has_more":    next_cursor is not None,
        "next_cursor": next_cursor,
    }


async def get_message(msg_id: str, user_id: str) -> dict:
    table = get_table(settings.dynamo_messages_table)
    response = table.get_item(Key={"msgId": msg_id})
    item = response.get("Item")

    if not item:
        raise HTTPException(status_code=404, detail="Message not found")

    # Verify ownership via session
    await get_session(item["sessionId"], user_id)
    return db_to_message(item)


async def patch_message(msg_id: str, user_id: str, state: str | None, content: str | None) -> dict:
    # Verify ownership
    await get_message(msg_id, user_id)

    table = get_table(settings.dynamo_messages_table)
    expressions = ["#st = :state", "updatedAt = :now"]
    names  = {"#st": "state"}
    values = {":now": now_iso()}

    if state:
        values[":state"] = state
    else:
        values[":state"] = "active"

    if content is not None:
        expressions.append("content = :content")
        values[":content"] = content

    response = table.update_item(
        Key={"msgId": msg_id},
        UpdateExpression="SET " + ", ".join(expressions),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return db_to_message(response["Attributes"])


async def delete_message(msg_id: str, user_id: str):
    """Soft delete — sets state to deleted, preserves the record."""
    await patch_message(msg_id, user_id, state="deleted", content=None)


# ─── Usage Stats ─────────────────────────────────────────────────────────────

async def get_usage_stats(user_id: str) -> dict:
    """Aggregate token usage for a user across all messages."""
    table = get_table(settings.dynamo_messages_table)

    total_input_tokens = 0
    total_output_tokens = 0
    total_messages = 0
    daily: dict[str, dict] = {}
    model_tokens: dict[str, int] = {}

    kwargs = {
        "IndexName": "userId-createdAt-index",
        "KeyConditionExpression": Key("userId").eq(user_id),
        "FilterExpression": "#role = :assistant",
        "ExpressionAttributeNames": {"#role": "role"},
        "ExpressionAttributeValues": {":assistant": "assistant"},
    }

    while True:
        response = table.query(**kwargs)
        for item in response.get("Items", []):
            input_tokens  = int(item.get("inputTokens")  or 0)
            output_tokens = int(item.get("outputTokens") or 0)
            model_id      = item.get("modelId", "amazon.nova-micro-v1:0")
            created_at    = item.get("createdAt", "")
            date_str      = created_at[:10] if len(created_at) >= 10 else "unknown"

            total_input_tokens  += input_tokens
            total_output_tokens += output_tokens
            total_messages      += 1

            if date_str not in daily:
                daily[date_str] = {"inputTokens": 0, "outputTokens": 0, "messageCount": 0}
            daily[date_str]["inputTokens"]  += input_tokens
            daily[date_str]["outputTokens"] += output_tokens
            daily[date_str]["messageCount"] += 1

            model_tokens[model_id] = model_tokens.get(model_id, 0) + input_tokens + output_tokens

        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    total_tokens = total_input_tokens + total_output_tokens
    estimated_cost = (total_input_tokens / 1000) * 0.000035 + (total_output_tokens / 1000) * 0.000035

    daily_usage = [
        {
            "date": date,
            "inputTokens": v["inputTokens"],
            "outputTokens": v["outputTokens"],
            "messageCount": v["messageCount"],
        }
        for date, v in sorted(daily.items())
        if date != "unknown"
    ]

    grand_total = total_tokens or 1  # avoid division by zero
    model_breakdown = [
        {
            "modelId": mid,
            "tokenCount": count,
            "percentage": round(count / grand_total * 100),
        }
        for mid, count in sorted(model_tokens.items(), key=lambda x: -x[1])
    ]

    return {
        "total_messages":       total_messages,
        "total_input_tokens":   total_input_tokens,
        "total_output_tokens":  total_output_tokens,
        "total_tokens":         total_tokens,
        "estimated_cost_usd":   round(estimated_cost, 6),
        "daily_usage":          daily_usage,
        "model_breakdown":      model_breakdown,
    }
