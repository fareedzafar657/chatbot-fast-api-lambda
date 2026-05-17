import boto3
import base64
import json
import logging
from boto3.dynamodb.conditions import Key
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import HTTPException, status
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ─── GSI names ───────────────────────────────────────────────────────────────
IDX_USER_UPDATED    = "userId-updatedAt-index"
IDX_SESSION_CREATED = "sessionId-createdAt-index"
IDX_BRANCH_CREATED  = "branchId-createdAt-index"
IDX_USER_CREATED    = "userId-createdAt-index"

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


def _paginate_query(table, **kwargs):
    """Yield every item from a paginated DynamoDB query."""
    while True:
        resp = table.query(**kwargs)
        yield from resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def encode_cursor(last_evaluated_key: dict) -> str:
    return base64.b64encode(json.dumps(last_evaluated_key).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    try:
        return json.loads(base64.b64decode(cursor.encode()).decode())
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        logger.warning(f"Cursor decode failed: {e}")
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

    branches_table = get_table(settings.dynamo_branches_table)
    messages_table = get_table(settings.dynamo_messages_table)

    # Collect all branch IDs for this session
    branch_ids = [
        item["branchId"]
        for item in _paginate_query(
            branches_table,
            IndexName=IDX_SESSION_CREATED,
            KeyConditionExpression=Key("sessionId").eq(session_id),
            ProjectionExpression="branchId",
        )
    ]

    # Delete all messages across all branches
    with messages_table.batch_writer() as batch:
        for branch_id in branch_ids:
            for item in _paginate_query(
                messages_table,
                IndexName=IDX_BRANCH_CREATED,
                KeyConditionExpression=Key("branchId").eq(branch_id),
                ProjectionExpression="msgId",
            ):
                batch.delete_item(Key={"msgId": item["msgId"]})

    # Delete all branches
    with branches_table.batch_writer() as batch:
        for branch_id in branch_ids:
            batch.delete_item(Key={"branchId": branch_id})

    # Delete the session
    get_table(settings.dynamo_sessions_table).delete_item(Key={"sessionId": session_id})


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


async def get_branch(branch_id: str, user_id: str) -> dict:
    table = get_table(settings.dynamo_branches_table)
    response = table.get_item(Key={"branchId": branch_id})
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Branch not found")
    await get_session(item["sessionId"], user_id)
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
        response = messages_table.get_item(Key={"msgId": msg_id})
        if "Item" in response:
            original_msg = response["Item"]
            new_msg_id = f"msg_{uuid4()}"
            duplicated_msg = {
                **original_msg,
                "msgId": new_msg_id,
                "branchId": branch_id,
                "createdAt": now_iso(),
            }
            messages_table.put_item(Item=duplicated_msg)
            new_msg_ids.append(new_msg_id)

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
    await get_branch(branch_id, user_id)

    table = get_table(settings.dynamo_messages_table)

    kwargs = {
        "IndexName": IDX_BRANCH_CREATED,
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
        "count":       len(items),
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

    await get_session(item["sessionId"], user_id)
    return db_to_message(item)


async def patch_message(msg_id: str, user_id: str, state: str | None, content: str | None) -> dict:
    await get_message(msg_id, user_id)

    table = get_table(settings.dynamo_messages_table)
    expressions = ["updatedAt = :now"]
    names  = {}
    values = {":now": now_iso()}

    if state is not None:
        expressions.append("#st = :state")
        names["#st"] = "state"
        values[":state"] = state

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

MODEL_COSTS: dict[str, tuple[float, float]] = {
    # (input_rate, output_rate) per 1K tokens
    "amazon.nova-micro-v1:0":    (0.000035,  0.00014),
    "amazon.nova-lite-v1:0":     (0.00006,   0.00024),
    "us.amazon.nova-pro-v1:0":   (0.0008,    0.0032),
    "claude-haiku-4-5-20251001": (0.0008,    0.004),
    "claude-sonnet-4-5":         (0.003,     0.015),
    "claude-opus-4-7":           (0.015,     0.075),
    "gemini-2.5-flash":          (0.0000375, 0.00015),
    "gemini-2.5-pro":            (0.00125,   0.005),
}
_DEFAULT_COST = (0.000035, 0.00014)  # Nova Micro fallback for unknown models


async def get_usage_stats(user_id: str) -> dict:
    """Aggregate token usage for a user across all messages."""
    table = get_table(settings.dynamo_messages_table)

    total_input_tokens = 0
    total_output_tokens = 0
    total_messages = 0
    estimated_cost = 0.0
    daily: dict[str, dict] = {}
    model_tokens: dict[str, int] = {}

    # Query all messages by userId — only user messages have userId on them
    # (assistant messages didn't store userId historically). We identify assistant
    # turns by the presence of inputTokens/outputTokens rather than role filter.
    kwargs = {
        "IndexName": IDX_USER_CREATED,
        "KeyConditionExpression": Key("userId").eq(user_id),
    }

    while True:
        response = table.query(**kwargs)
        for item in response.get("Items", []):
            input_tokens  = int(item.get("inputTokens")  or 0)
            output_tokens = int(item.get("outputTokens") or 0)

            # Skip user messages — they have no token data
            if input_tokens == 0 and output_tokens == 0:
                continue

            model_id      = item.get("modelId", "amazon.nova-micro-v1:0")
            created_at    = item.get("createdAt", "")
            date_str      = created_at[:10] if len(created_at) >= 10 else "unknown"

            total_input_tokens  += input_tokens
            total_output_tokens += output_tokens
            total_messages      += 1

            if model_id not in MODEL_COSTS:
                logger.warning(f"Unknown modelId '{model_id}' — using default cost rate")
            in_rate, out_rate = MODEL_COSTS.get(model_id, _DEFAULT_COST)
            estimated_cost += (input_tokens / 1000) * in_rate + (output_tokens / 1000) * out_rate

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

    daily_usage = [
        {
            "date":          date,
            "input_tokens":  v["inputTokens"],
            "output_tokens": v["outputTokens"],
            "message_count": v["messageCount"],
        }
        for date, v in sorted(daily.items())
        if date != "unknown"
    ]

    grand_total = total_tokens or 1  # avoid division by zero
    model_breakdown = [
        {
            "model_id":    mid,
            "token_count": count,
            "percentage":  round(count / grand_total * 100),
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
