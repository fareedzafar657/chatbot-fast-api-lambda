"""Message CRUD and usage stats."""
import logging
from boto3.dynamodb.conditions import Key
from fastapi import HTTPException
from app.services.db_base import (
    MESSAGES_TABLE,
    IDX_BRANCH_CREATED, IDX_USER_CREATED,
    get_table, now_iso,
    encode_cursor, decode_cursor,
    db_to_message,
)
from app.services.db_sessions import get_session

logger = logging.getLogger(__name__)

# ─── Usage Stats constants ────────────────────────────────────────────────────
MODEL_COSTS: dict[str, tuple[float, float]] = {
    # (input_rate, output_rate) per 1K tokens
    "us.amazon.nova-pro-v1:0":   (0.0008,    0.0032),
    "claude-haiku-4-5-20251001": (0.0008,    0.004),
    "claude-sonnet-4-5":         (0.003,     0.015),
    "claude-opus-4-7":           (0.015,     0.075),
    "gemini-2.5-flash":          (0.0000375, 0.00015),
    "gemini-2.5-pro":            (0.00125,   0.005),
}
MAX_STAT_ITEMS = 5_000
UNKNOWN_MODEL_ID = "unknown"


async def list_messages(
    branch_id: str,
    user_id: str,
    page_size: int,
    cursor: str | None,
    include_deleted: bool = False,
) -> dict:
    from app.services.db_branches import get_branch
    await get_branch(branch_id, user_id)

    table = get_table(MESSAGES_TABLE)

    kwargs = {
        "IndexName": IDX_BRANCH_CREATED,
        "KeyConditionExpression": Key("branchId").eq(branch_id),
        "Limit": page_size,
        # newest first so latest messages (incl. summaries) load on page 1.
        # Successive cursor pages move backward in time (page 2 = older than page 1).
        "ScanIndexForward": False,
    }

    if not include_deleted:
        # NOTE: DynamoDB applies FilterExpression AFTER Limit, so a returned page
        # may have fewer than page_size items even when has_more is true.
        # Frontend should drive "load more" off has_more, not items.length.
        kwargs["FilterExpression"] = "attribute_not_exists(#st) OR #st <> :deleted"
        kwargs["ExpressionAttributeNames"] = {"#st": "state"}
        kwargs["ExpressionAttributeValues"] = {":deleted": "deleted"}

    if cursor:
        kwargs["ExclusiveStartKey"] = decode_cursor(cursor)

    response = table.query(**kwargs)
    # Reverse so items within the page are in ascending (oldest→newest) order.
    items = [db_to_message(i) for i in reversed(response.get("Items", []))]

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


async def get_message(msg_id: str, user_id: str) -> dict:
    table = get_table(MESSAGES_TABLE)
    response = table.get_item(Key={"msgId": msg_id})
    item = response.get("Item")

    if not item:
        raise HTTPException(status_code=404, detail="Message not found")

    await get_session(item["sessionId"], user_id)
    return db_to_message(item)


async def patch_message(msg_id: str, user_id: str, state: str | None, content: str | None) -> dict:
    await get_message(msg_id, user_id)

    table = get_table(MESSAGES_TABLE)
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


# ─── Usage Stats ──────────────────────────────────────────────────────────────

def _estimated_tokens(item: dict) -> tuple[int, int]:
    """
    Per-message (input_tokens, output_tokens) for usage stats.

    Uses stored values when present; otherwise estimates from content as char/4
    attributed to input (user role) or output (assistant role).
    """
    stored_in  = item.get("inputTokens")
    stored_out = item.get("outputTokens")
    if stored_in is not None or stored_out is not None:
        return int(stored_in or 0), int(stored_out or 0)
    estimate = len(item.get("content") or "") // 4
    return (0, estimate) if item.get("role") == "assistant" else (estimate, 0)


async def get_usage_stats(user_id: str) -> dict:
    table = get_table(MESSAGES_TABLE)

    total_input_tokens = 0
    total_output_tokens = 0
    total_messages = 0
    estimated_cost = 0.0
    daily: dict[str, dict] = {}
    model_tokens: dict[str, int] = {}

    # Query messages by userId — only user messages carry userId historically.
    # Assistant turns are inferred from the presence of token counts; for messages
    # without stored counts, _estimated_tokens() falls back to a content-based estimate.
    kwargs = {
        "IndexName": IDX_USER_CREATED,
        "KeyConditionExpression": Key("userId").eq(user_id),
        "ScanIndexForward": False,  # newest first so the cap covers recent history
    }

    processed = 0
    while True:
        response = table.query(**kwargs)
        items = response.get("Items", [])
        processed += len(items)

        for item in items:
            input_tokens, output_tokens = _estimated_tokens(item)

            if input_tokens == 0 and output_tokens == 0:
                continue

            model_id   = item.get("modelId") or UNKNOWN_MODEL_ID
            created_at = item.get("createdAt", "")
            date_str   = created_at[:10] if len(created_at) >= 10 else "unknown"

            total_input_tokens  += input_tokens
            total_output_tokens += output_tokens
            total_messages      += 1

            rates = MODEL_COSTS.get(model_id)
            if rates is None:
                if model_id != UNKNOWN_MODEL_ID:
                    logger.warning(f"Unknown modelId '{model_id}' — counting tokens with 0 cost")
                # Unknown model: tokens still count, cost is 0 (we don't make up a rate)
            else:
                in_rate, out_rate = rates
                estimated_cost += (input_tokens / 1000) * in_rate + (output_tokens / 1000) * out_rate

            if date_str not in daily:
                daily[date_str] = {"inputTokens": 0, "outputTokens": 0, "messageCount": 0}
            daily[date_str]["inputTokens"]  += input_tokens
            daily[date_str]["outputTokens"] += output_tokens
            daily[date_str]["messageCount"] += 1

            model_tokens[model_id] = model_tokens.get(model_id, 0) + input_tokens + output_tokens

        if "LastEvaluatedKey" not in response or processed >= MAX_STAT_ITEMS:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    truncated = processed >= MAX_STAT_ITEMS and "LastEvaluatedKey" in response
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
        "truncated":            truncated,
    }
