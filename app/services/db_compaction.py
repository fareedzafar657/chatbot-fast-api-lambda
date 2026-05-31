"""Compact messages into an AI summary, and delete/restore compactions."""
import json
import logging
import boto3
import httpx
from uuid import uuid4
from boto3.dynamodb.types import TypeSerializer
from fastapi import HTTPException, status
from app.config import get_settings
from app.services.db_base import (
    BRANCHES_TABLE, MESSAGES_TABLE,
    get_dynamodb, get_table, now_iso,
    _batch_get_messages, db_to_message,
)
from app.services.db_sessions import get_session

logger = logging.getLogger(__name__)
settings = get_settings()

_COMPACT_SYSTEM_PROMPT = (
    "Summarize this conversation compactly, preserving all key facts, "
    "decisions, and context so the conversation can continue naturally. "
    "Be concise but complete."
)

# Default Bedrock model — always allowed for every user. Only models other than this
# require the caller to be on the demo allowlist.
_DEFAULT_BEDROCK_MODEL = "us.amazon.nova-pro-v1:0"


# ─── Token estimation ─────────────────────────────────────────────────────────

def _estimated_tokens(item: dict) -> tuple[int, int]:
    stored_in  = item.get("inputTokens")
    stored_out = item.get("outputTokens")
    if stored_in is not None or stored_out is not None:
        return int(stored_in or 0), int(stored_out or 0)
    estimate = len(item.get("content") or "") // 4
    return (0, estimate) if item.get("role") == "assistant" else (estimate, 0)


# ─── Provider summarizers ────────────────────────────────────────────────────

def _flatten_conversation(messages: list) -> str:
    """Render the messages as a single transcript. Sending the selection as raw alternating
    turns ending on an assistant message makes models treat the dialogue as complete (Nova
    and Gemini return empty; recent Claude rejects the implied prefill with 400). Folding it
    into one user turn asks for a summary instead of a continuation."""
    return "\n\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][0]['text']}"
        for m in messages
    )


def _summarize_anthropic(model: str, api_key: str, messages: list) -> tuple[str, int]:
    conv_text = _flatten_conversation(messages)
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "system": _COMPACT_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": conv_text}],
            "max_tokens": 512,
        },
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
    all_text    = [p["text"] for p in parts if p.get("text")]
    non_thought = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
    text_parts  = non_thought or all_text
    if not text_parts:
        raise ValueError("Gemini response has no text content")
    return text_parts[0]


def _summarize_gemini(model: str, api_key: str, messages: list) -> tuple[str, int]:
    conv_text = _flatten_conversation(messages)
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
    # Bedrock — use caller-supplied model if provided, otherwise default to Nova Pro
    bedrock_model = model or _DEFAULT_BEDROCK_MODEL
    conv_text = _flatten_conversation(converse_messages)
    bedrock      = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    bedrock_resp = bedrock.converse(
        modelId=bedrock_model,
        system=[{"text": _COMPACT_SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": conv_text}]}],
        inferenceConfig={"maxTokens": 1024},
    )
    content_blocks = bedrock_resp["output"]["message"]["content"]
    text = content_blocks[0].get("text") or "" if content_blocks else ""
    if not text:
        raise ValueError("Bedrock returned an empty summary")
    return text, bedrock_resp["usage"]["outputTokens"]


# ─── Validation & DB helpers ──────────────────────────────────────────────────

async def _validate_compact_request(
    branches_table, branch_id: str, user_id: str, msg_ids: list[str]
) -> tuple[dict, list[dict]]:
    branch_resp = branches_table.get_item(Key={"branchId": branch_id})
    branch_item = branch_resp.get("Item")
    if not branch_item:
        raise HTTPException(status_code=404, detail="Branch not found")
    await get_session(branch_item["sessionId"], user_id)

    fetched = _batch_get_messages(msg_ids)

    missing = [mid for mid in msg_ids if mid not in fetched]
    if missing:
        raise HTTPException(status_code=404, detail=f"Messages not found: {missing[:5]}")

    # Verify all messages belong to this branch
    wrong_branch = [mid for mid, m in fetched.items() if m.get("branchId") != branch_id]
    if wrong_branch:
        raise HTTPException(status_code=400, detail="Some messages do not belong to this branch")

    # Sort by createdAt so the AI receives messages in chronological order
    msg_items = sorted(fetched.values(), key=lambda m: m.get("createdAt", ""))

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
    now = now_iso()
    for msg_id in msg_ids:
        messages_table.update_item(
            Key={"msgId": msg_id},
            UpdateExpression="SET #st = :compacted, compactedBy = :cb, updatedAt = :now",
            ExpressionAttributeNames={"#st": "state"},
            ExpressionAttributeValues={":compacted": "compacted", ":cb": compacted_by, ":now": now},
        )


# ─── Public API ───────────────────────────────────────────────────────────────

async def compact_messages(
    branch_id: str,
    user_id: str,
    msg_ids: list[str],
    name: str,
    user_email: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict:
    # Validate demo-model access: the default Bedrock model is always allowed. Only a
    # non-default Bedrock model requires the caller to be on the demo allowlist.
    if not provider and model and model != _DEFAULT_BEDROCK_MODEL:
        if user_email not in settings.demo_allowed_emails:
            raise HTTPException(status_code=403, detail="Access denied to requested model")

    branches_table = get_table(BRANCHES_TABLE)
    messages_table = get_table(MESSAGES_TABLE)

    branch_item, msg_items = await _validate_compact_request(
        branches_table, branch_id, user_id, msg_ids
    )

    tokens_before = sum(i + o for i, o in (_estimated_tokens(m) for m in msg_items))

    converse_messages = [
        {"role": m["role"], "content": [{"text": m.get("content") or ""}]}
        for m in msg_items
        if m.get("content")
    ]

    if not converse_messages:
        raise HTTPException(status_code=400, detail="No messages with content to compact")

    try:
        summary_text, tokens_after = _summarize(converse_messages, provider, model, api_key)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Summarization failed", extra={"provider": provider or "bedrock"})
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

    compacted_by = {"summaryMsgId": summary_item["msgId"], "name": name}
    _mark_originals_as_compacted(messages_table, msg_ids, compacted_by)

    return {
        "summary_message": db_to_message(summary_item),
        "tokens_before":   tokens_before,
        "tokens_after":    tokens_after,
    }


async def delete_compaction(branch_id: str, summary_msg_id: str, user_id: str) -> dict:
    branches_table = get_table(BRANCHES_TABLE)
    messages_table = get_table(MESSAGES_TABLE)

    branch_resp = branches_table.get_item(Key={"branchId": branch_id})
    branch_item = branch_resp.get("Item")
    if not branch_item:
        raise HTTPException(status_code=404, detail="Branch not found")
    await get_session(branch_item["sessionId"], user_id)

    summary_resp = messages_table.get_item(Key={"msgId": summary_msg_id})
    summary = summary_resp.get("Item")
    if not summary:
        raise HTTPException(status_code=404, detail="Compaction summary not found")
    if summary.get("type") != "compaction-summary":
        raise HTTPException(status_code=400, detail="Message is not a compaction summary")
    if summary.get("branchId") != branch_id:
        raise HTTPException(status_code=400, detail="Message does not belong to this branch")

    # Dedupe + drop the summary's own id — TransactWriteItems rejects two ops on one key.
    original_msg_ids = list(dict.fromkeys(
        mid for mid in summary.get("originalMsgIds", []) if mid != summary_msg_id
    ))
    now = now_iso()

    # Restore originals via TransactWriteItems (atomic, max 100 items per transaction).
    # We chunk to 99 originals + 1 summary-delete in the final batch.
    # If a later batch fails, earlier batches stay restored — operator must reconcile.
    # Low-level client, not get_dynamodb().meta.client — the resource client re-marshals our
    # already-serialized keys into {"S": {"S": ...}} → "key element does not match the schema".
    client     = boto3.client("dynamodb", region_name=settings.aws_region)
    serializer = TypeSerializer()
    BATCH = 99

    def _restore_update(msg_id: str) -> dict:
        return {
            "Update": {
                "TableName": MESSAGES_TABLE,
                "Key": {"msgId": serializer.serialize(msg_id)},
                "UpdateExpression": "SET #st = :active, updatedAt = :now REMOVE compactedBy",
                "ExpressionAttributeNames":  {"#st": "state"},
                "ExpressionAttributeValues": {
                    ":active": serializer.serialize("active"),
                    ":now":    serializer.serialize(now),
                },
            }
        }

    summary_delete = {
        "Update": {
            "TableName": MESSAGES_TABLE,
            "Key": {"msgId": serializer.serialize(summary_msg_id)},
            "UpdateExpression": "SET #st = :deleted, updatedAt = :now",
            "ExpressionAttributeNames":  {"#st": "state"},
            "ExpressionAttributeValues": {
                ":deleted": serializer.serialize("deleted"),
                ":now":     serializer.serialize(now),
            },
        }
    }

    if not original_msg_ids:
        client.transact_write_items(TransactItems=[summary_delete])
    else:
        for i in range(0, len(original_msg_ids), BATCH):
            chunk = original_msg_ids[i:i + BATCH]
            items = [_restore_update(mid) for mid in chunk]
            if i + BATCH >= len(original_msg_ids):
                items.append(summary_delete)
            client.transact_write_items(TransactItems=items)

    # Re-fetch restored items for the response (transactions don't return values)
    restored_items = _batch_get_messages(original_msg_ids).values()

    return {"restored_messages": [db_to_message(item) for item in restored_items]}
