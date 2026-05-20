import boto3
import base64
import json
import logging
import httpx
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
        "msg_id":           item.get("msgId"),
        "session_id":       item.get("sessionId"),
        "branch_id":        item.get("branchId"),
        "role":             item.get("role"),
        "content":          item.get("content"),
        "state":            item.get("state", "active"),
        "user_id":          item.get("userId", ""),
        "parent_msg_id":    item.get("parentMsgId"),
        "input_tokens":     int(item["inputTokens"])  if item.get("inputTokens")  else None,
        "output_tokens":    int(item["outputTokens"]) if item.get("outputTokens") else None,
        "created_at":       item.get("createdAt"),
        "updated_at":       item.get("updatedAt"),
        "type":             item.get("type"),
        "compaction_name":  item.get("compactionName"),
        "original_msg_ids": item.get("originalMsgIds"),
        "tokens_before":    int(item["tokensBefore"]) if item.get("tokensBefore") else None,
        "tokens_after":     int(item["tokensAfter"])  if item.get("tokensAfter")  else None,
        "compacted_by":     item.get("compactedBy"),
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

    # Validate: first selected message must be from user OR be a compaction summary
    # (summaries are assistant-role but serve as context, not a regular AI reply)
    selected_msg_ids = data.get("selected_msg_ids", [])
    if selected_msg_ids:
        first_msg_id = selected_msg_ids[0]
        first_msg = await get_message(first_msg_id, user_id)
        if first_msg["role"] != "user" and first_msg.get("type") != "compaction-summary":
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
            # Clear token counts — duplicated messages didn't consume new tokens
            duplicated_msg.pop("inputTokens", None)
            duplicated_msg.pop("outputTokens", None)
            # Forked copies of compacted originals become fresh active messages
            if original_msg.get("state") == "compacted":
                duplicated_msg["state"] = "active"
                duplicated_msg.pop("compactedBy", None)
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


async def cherry_pick_messages(branch_id: str, user_id: str, source_msg_ids: list[str]) -> dict:
    # Verify branch ownership
    target_branch = await get_branch(branch_id, user_id)

    # Duplicate each source message into the target branch
    messages_table = get_table(settings.dynamo_messages_table)
    new_msg_ids = []
    duplicated_items = []

    for msg_id in source_msg_ids:
        response = messages_table.get_item(Key={"msgId": msg_id})
        if "Item" in response:
            original_msg = response["Item"]
            if original_msg.get("userId") != user_id:
                raise HTTPException(status_code=403, detail="Access denied to source message")
            new_msg_id = f"msg_{uuid4()}"
            duplicated_msg = {
                **original_msg,
                "msgId": new_msg_id,
                "branchId": branch_id,
                "createdAt": now_iso(),
            }
            # Clear token counts — duplicated messages didn't consume new tokens
            duplicated_msg.pop("inputTokens", None)
            duplicated_msg.pop("outputTokens", None)
            messages_table.put_item(Item=duplicated_msg)
            new_msg_ids.append(new_msg_id)
            duplicated_items.append(duplicated_msg)

    # Append new message IDs to the target branch's selectedMsgIds
    branches_table = get_table(settings.dynamo_branches_table)
    branches_table.update_item(
        Key={"branchId": branch_id},
        UpdateExpression="SET selectedMsgIds = list_append(selectedMsgIds, :new_ids)",
        ExpressionAttributeValues={":new_ids": new_msg_ids},
    )

    # Fetch the updated branch
    response = branches_table.get_item(Key={"branchId": branch_id})
    updated_branch = db_to_branch(response["Item"])

    # Convert duplicated messages to API format
    new_messages = [db_to_message(item) for item in duplicated_items]

    return {
        "branch":       updated_branch,
        "new_messages": new_messages,
    }


_COMPACT_SYSTEM_PROMPT = (
    "Summarize this conversation compactly, preserving all key facts, "
    "decisions, and context so the conversation can continue naturally. "
    "Be concise but complete."
)


def _summarize_anthropic(model: str, api_key: str, messages: list) -> tuple[str, int]:
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        json={"model": model, "system": _COMPACT_SYSTEM_PROMPT, "messages": messages, "max_tokens": 512},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"], data["usage"]["output_tokens"]


def _extract_gemini_text(data: dict) -> str:
    """Extract the response text from a Gemini generateContent response body."""
    candidate = data.get("candidates", [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    if not parts:
        finish = candidate.get("finishReason", "unknown")
        logger.error(f"Gemini no content parts (finishReason={finish!r}): {json.dumps(data)[:500]}")
        raise ValueError(f"Gemini returned no content (finishReason={finish!r})")
    # Gemini 2.5 thinking models prepend thought parts (thought=True) before the real response
    all_text   = [p["text"] for p in parts if p.get("text")]
    non_thought = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
    text_parts  = non_thought or all_text
    if not text_parts:
        raise ValueError("Gemini response has no text content")
    return text_parts[0]


def _summarize_gemini(model: str, api_key: str, messages: list) -> tuple[str, int]:
    # Present the conversation as a single user turn rather than multi-turn contents.
    # When contents ends with a model-role entry, Gemini returns STOP with zero output
    # because it considers the conversation already complete — it won't continue on its own.
    conv_text = "\n\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][0]['text']}"
        for m in messages
    )
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={
            "systemInstruction": {"parts": [{"text": _COMPACT_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": conv_text}]}],
            "generationConfig": {"maxOutputTokens": 2048},
        },
        timeout=60,
    )
    if not resp.is_success:
        logger.error(f"Gemini API error {resp.status_code} for model={model!r}: {resp.text[:500]}")
    resp.raise_for_status()
    data = resp.json()
    tokens = data.get("usageMetadata", {}).get("candidatesTokenCount", 0)
    return _extract_gemini_text(data), tokens


async def _validate_compact_request(
    branches_table, messages_table, branch_id: str, user_id: str, msg_ids: list[str]
) -> tuple[dict, list[dict]]:
    """Verify ownership, validate msg_ids belong to the branch, fetch + sort the messages.
    Returns (branch_item, sorted msg_items)."""
    branch_resp = branches_table.get_item(Key={"branchId": branch_id})
    branch_item = branch_resp.get("Item")
    if not branch_item:
        raise HTTPException(status_code=404, detail="Branch not found")
    await get_session(branch_item["sessionId"], user_id)

    selected_msg_ids = branch_item.get("selectedMsgIds", [])
    if not set(msg_ids).issubset(set(selected_msg_ids)):
        raise HTTPException(status_code=400, detail="Some messages do not belong to this branch")

    # Fetch all messages in batches of 100 (DynamoDB batch_get_item limit)
    fetched: dict[str, dict] = {}
    for i in range(0, len(msg_ids), 100):
        chunk = msg_ids[i:i + 100]
        resp = get_dynamodb().batch_get_item(
            RequestItems={settings.dynamo_messages_table: {"Keys": [{"msgId": mid} for mid in chunk]}}
        )
        for item in resp.get("Responses", {}).get(settings.dynamo_messages_table, []):
            fetched[item["msgId"]] = item

    missing = [mid for mid in msg_ids if mid not in fetched]
    if missing:
        raise HTTPException(status_code=404, detail=f"Messages not found: {missing[:5]}")

    msg_items = list(fetched.values())

    # Sort by position in selectedMsgIds so the AI receives messages in chronological order
    msg_id_to_pos = {mid: i for i, mid in enumerate(selected_msg_ids)}
    msg_items.sort(key=lambda m: msg_id_to_pos.get(m["msgId"], 0))

    if msg_items and msg_items[0].get("role") != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="First selected message must be from user"
        )

    return branch_item, msg_items


def _build_summary_item(
    branch_item: dict, branch_id: str, user_id: str,
    summary_text: str, name: str, msg_ids: list[str],
    tokens_before: int, tokens_after: int,
) -> dict:
    """Construct the DynamoDB item for the compaction-summary message."""
    now = now_iso()
    return {
        "msgId":          f"msg_{uuid4()}",
        "sessionId":      branch_item["sessionId"],
        "branchId":       branch_id,
        "role":           "assistant",
        "type":           "compaction-summary",
        "content":        summary_text,
        "compactionName": name,
        "originalMsgIds": msg_ids,
        "tokensBefore":   tokens_before,
        "tokensAfter":    tokens_after,
        "state":          "active",
        "userId":         user_id,
        "createdAt":      now,
        "updatedAt":      now,
    }


def _mark_originals_as_compacted(messages_table, msg_ids: list[str], compacted_by: dict) -> None:
    """Set state=compacted and attach compactedBy metadata on each original message."""
    now = now_iso()
    for msg_id in msg_ids:
        messages_table.update_item(
            Key={"msgId": msg_id},
            UpdateExpression="SET #st = :compacted, compactedBy = :cb, updatedAt = :now",
            ExpressionAttributeNames={"#st": "state"},
            ExpressionAttributeValues={":compacted": "compacted", ":cb": compacted_by, ":now": now},
        )


def _summarize(
    converse_messages: list,
    provider: str | None,
    model: str | None,
    api_key: str | None,
) -> tuple[str, int]:
    """Route to the appropriate AI provider and return (summary_text, tokens_after)."""
    if provider == "anthropic" and model and api_key:
        return _summarize_anthropic(model, api_key, converse_messages)
    if provider == "gemini" and model and api_key:
        return _summarize_gemini(model, api_key, converse_messages)
    # Default: Bedrock Nova Lite
    bedrock      = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    bedrock_resp = bedrock.converse(
        modelId="amazon.nova-lite-v1:0",
        system=[{"text": _COMPACT_SYSTEM_PROMPT}],
        messages=converse_messages,
        inferenceConfig={"maxTokens": 512},
    )
    content_blocks = bedrock_resp["output"]["message"]["content"]
    return (content_blocks[0]["text"] if content_blocks else ""), bedrock_resp["usage"]["outputTokens"]


def _splice_branch_ids(ids: list[str], old_ids: list[str], new_ids: list[str], error_detail: str) -> list[str]:
    """Replace old_ids with new_ids in ids, preserving the position of the first old_id."""
    old_set    = set(old_ids)
    insert_idx = next((i for i, mid in enumerate(ids) if mid in old_set), None)
    if insert_idx is None:
        raise HTTPException(status_code=400, detail=error_detail)
    return [mid for mid in ids[:insert_idx]] + new_ids + [mid for mid in ids[insert_idx:] if mid not in old_set]


async def compact_messages(
    branch_id: str,
    user_id: str,
    msg_ids: list[str],
    name: str,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict:
    branches_table = get_table(settings.dynamo_branches_table)
    messages_table = get_table(settings.dynamo_messages_table)

    branch_item, msg_items = await _validate_compact_request(
        branches_table, messages_table, branch_id, user_id, msg_ids
    )

    tokens_before = sum(len(m.get("content") or "") for m in msg_items) // 4

    # Build messages in Bedrock/Anthropic format (role + content array)
    converse_messages = [
        {"role": m["role"], "content": [{"text": m.get("content") or ""}]}
        for m in msg_items
        if m.get("content")
    ]

    try:
        summary_text, tokens_after = _summarize(converse_messages, provider, model, api_key)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Summarization failed ({provider or 'bedrock'}): {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate summary"
        )

    if not summary_text.strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Provider returned an empty summary"
        )

    summary_item = _build_summary_item(
        branch_item, branch_id, user_id, summary_text, name, msg_ids, tokens_before, tokens_after
    )
    messages_table.put_item(Item=summary_item)

    # Replace compacted IDs with summary ID in selectedMsgIds (preserving order)
    selected_msg_ids = branch_item.get("selectedMsgIds", [])
    new_selected = _splice_branch_ids(
        selected_msg_ids, msg_ids, [summary_item["msgId"]], "Messages not found in branch"
    )
    branches_table.update_item(
        Key={"branchId": branch_id},
        UpdateExpression="SET selectedMsgIds = :new_ids",
        ExpressionAttributeValues={":new_ids": new_selected},
    )

    compacted_by = {"summaryMsgId": summary_item["msgId"], "name": name}
    _mark_originals_as_compacted(messages_table, msg_ids, compacted_by)

    return {
        "summary_message": db_to_message(summary_item),
        "tokens_before":   tokens_before,
        "tokens_after":    tokens_after,
    }


async def delete_compaction(branch_id: str, summary_msg_id: str, user_id: str) -> dict:
    branches_table = get_table(settings.dynamo_branches_table)
    messages_table = get_table(settings.dynamo_messages_table)

    # Verify branch ownership
    branch_resp = branches_table.get_item(Key={"branchId": branch_id})
    branch_item = branch_resp.get("Item")
    if not branch_item:
        raise HTTPException(status_code=404, detail="Branch not found")
    await get_session(branch_item["sessionId"], user_id)

    # Fetch and validate the summary message
    summary_resp = messages_table.get_item(Key={"msgId": summary_msg_id})
    summary = summary_resp.get("Item")
    if not summary:
        raise HTTPException(status_code=404, detail="Compaction summary not found")
    if summary.get("type") != "compaction-summary":
        raise HTTPException(status_code=400, detail="Message is not a compaction summary")
    if summary.get("branchId") != branch_id:
        raise HTTPException(status_code=400, detail="Message does not belong to this branch")

    original_msg_ids = summary.get("originalMsgIds", [])

    # Replace summary ID with original IDs in selectedMsgIds (preserving position)
    selected = branch_item.get("selectedMsgIds", [])
    new_selected = _splice_branch_ids(
        selected, [summary_msg_id], original_msg_ids, "Summary not found in branch selectedMsgIds"
    )
    branches_table.update_item(
        Key={"branchId": branch_id},
        UpdateExpression="SET selectedMsgIds = :new_ids",
        ExpressionAttributeValues={":new_ids": new_selected},
    )

    # Restore each original message to active state and remove compactedBy
    now = now_iso()
    restored_items = []
    for msg_id in original_msg_ids:
        resp = messages_table.update_item(
            Key={"msgId": msg_id},
            UpdateExpression="SET #st = :active, updatedAt = :now REMOVE compactedBy",
            ExpressionAttributeNames={"#st": "state"},
            ExpressionAttributeValues={":active": "active", ":now": now},
            ReturnValues="ALL_NEW",
        )
        if "Attributes" in resp:
            restored_items.append(resp["Attributes"])

    # Soft-delete the summary message
    messages_table.update_item(
        Key={"msgId": summary_msg_id},
        UpdateExpression="SET #st = :deleted, updatedAt = :now",
        ExpressionAttributeNames={"#st": "state"},
        ExpressionAttributeValues={":deleted": "deleted", ":now": now},
    )

    return {"restored_messages": [db_to_message(item) for item in restored_items]}


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
        "ScanIndexForward": False,   # newest first so latest messages (incl. summaries) load on page 1
    }

    if not include_deleted:
        kwargs["FilterExpression"] = "attribute_not_exists(#st) OR #st <> :deleted"
        kwargs["ExpressionAttributeNames"] = {"#st": "state"}
        kwargs["ExpressionAttributeValues"] = {":deleted": "deleted"}

    if cursor:
        kwargs["ExclusiveStartKey"] = decode_cursor(cursor)

    response = table.query(**kwargs)
    # Reverse so items are in ascending (oldest→newest) order for the frontend
    items = [db_to_message(i) for i in reversed(response.get("Items", []))]

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
