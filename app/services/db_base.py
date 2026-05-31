"""
DynamoDB client, table/index constants, shared helpers, and db→dict converters.
Does NOT contain any business logic — only infrastructure and data-mapping.
"""
import base64
import binascii
import json
import logging
import boto3
from datetime import datetime, timezone
from fastapi import HTTPException, status
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ─── Table names ─────────────────────────────────────────────────────────────
SESSIONS_TABLE = "chatbot_sessions"
BRANCHES_TABLE = "chatbot_branches"
MESSAGES_TABLE = "chatbot_messages"

# ─── GSI names ───────────────────────────────────────────────────────────────
IDX_USER_UPDATED    = "userId-updatedAt-index"
IDX_SESSION_CREATED = "sessionId-createdAt-index"
IDX_BRANCH_CREATED  = "branchId-createdAt-index"
IDX_USER_CREATED    = "userId-createdAt-index"

# ─── Client (singleton) ──────────────────────────────────────────────────────
_dynamodb = None


def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
    return _dynamodb


def get_table(name: str):
    return get_dynamodb().Table(name)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paginate_query(table, **kwargs):
    while True:
        resp = table.query(**kwargs)
        yield from resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _batch_get_messages(msg_ids: list[str]) -> dict[str, dict]:
    """Fetch messages by msgId, returned as {msgId: item}.

    Chunks into batches of 100 (DynamoDB batch_get_item key limit).
    Retries UnprocessedKeys until all keys are resolved.
    Missing ids are simply absent from the result — callers decide how to react.
    """
    fetched: dict[str, dict] = {}
    dynamodb = get_dynamodb()
    for i in range(0, len(msg_ids), 100):
        chunk = msg_ids[i:i + 100]
        pending: dict = {MESSAGES_TABLE: {"Keys": [{"msgId": mid} for mid in chunk]}}
        while pending:
            resp = dynamodb.batch_get_item(RequestItems=pending)
            for item in resp.get("Responses", {}).get(MESSAGES_TABLE, []):
                fetched[item["msgId"]] = item
            pending = resp.get("UnprocessedKeys", {})
    return fetched


def encode_cursor(last_evaluated_key: dict) -> str:
    return base64.b64encode(json.dumps(last_evaluated_key).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    try:
        return json.loads(base64.b64decode(cursor.encode()).decode())
    except (ValueError, binascii.Error) as e:
        logger.warning(f"Cursor decode failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor"
        )


# ─── DB → dict converters ────────────────────────────────────────────────────

def db_to_message(item: dict) -> dict:
    result = {
        "msg_id":        item.get("msgId"),
        "session_id":    item.get("sessionId"),
        "branch_id":     item.get("branchId"),
        "role":          item.get("role"),
        "content":       item.get("content"),
        "state":         item.get("state", "active"),
        "user_id":       item.get("userId", ""),
        "parent_msg_id": item.get("parentMsgId"),
        "input_tokens":  int(item["inputTokens"])  if item.get("inputTokens")  is not None else None,
        "output_tokens": int(item["outputTokens"]) if item.get("outputTokens") is not None else None,
        "created_at":    item.get("createdAt"),
        "updated_at":    item.get("updatedAt"),
        "type":          item.get("type"),
        "compaction":    None,
        "compacted_by":  None,
    }
    if item.get("type") == "compaction-summary":
        result["compaction"] = {
            "name":             item.get("compactionName"),
            "original_msg_ids": item.get("originalMsgIds"),
            "tokens_before":    int(item["tokensBefore"]) if item.get("tokensBefore") is not None else None,
            "tokens_after":     int(item["tokensAfter"])  if item.get("tokensAfter")  is not None else None,
        }
    if item.get("compactedBy"):
        cb = item["compactedBy"]
        result["compacted_by"] = {
            "summary_msg_id": cb.get("summaryMsgId"),
            "name":           cb.get("name"),
        }
    return result


def db_to_branch(item: dict) -> dict:
    return {
        "branch_id":        item.get("branchId"),
        "session_id":       item.get("sessionId"),
        "parent_branch_id": item.get("parentBranchId"),
        "parent_msg_id":    item.get("parentMsgId"),
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
