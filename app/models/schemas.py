from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


# ─── Enums ───────────────────────────────────────────────────────────────────

MessageRole  = Literal["user", "assistant"]
MessageState = Literal["active", "stopped", "edited", "deleted"]


# ─── Message ─────────────────────────────────────────────────────────────────

class Message(BaseModel):
    msg_id:       str
    session_id:   str
    branch_id:    str
    role:         MessageRole
    content:      str
    state:        MessageState
    user_id:      str
    parent_msg_id: Optional[str] = None
    input_tokens:  Optional[int] = None
    output_tokens: Optional[int] = None
    created_at:   str
    updated_at:   str


class PatchMessageRequest(BaseModel):
    state:   Optional[MessageState] = None
    content: Optional[str] = None          # only used when state = edited

    model_config = {"json_schema_extra": {
        "examples": [
            {"state": "stopped"},
            {"state": "edited", "content": "Updated message text"},
            {"state": "deleted"},
            {"state": "active"},
        ]
    }}


# ─── Branch ──────────────────────────────────────────────────────────────────

class Branch(BaseModel):
    branch_id:        str
    session_id:       str
    parent_branch_id: Optional[str] = None
    parent_msg_id:    Optional[str] = None
    selected_msg_ids: list[str] = []
    label:            str
    created_at:       str


class ForkBranchRequest(BaseModel):
    session_id:       str
    parent_branch_id: str
    parent_msg_id:    Optional[str] = None
    selected_msg_ids: list[str] = Field(..., min_length=1)
    label:            Optional[str] = None

    model_config = {"json_schema_extra": {
        "examples": [{
            "session_id": "session_abc",
            "parent_branch_id": "branch_xyz",
            "parent_msg_id": "msg_123",
            "selected_msg_ids": ["msg_001", "msg_003"],
            "label": "without that tangent"
        }]
    }}


class UpdateActiveBranchRequest(BaseModel):
    branch_id: str


# ─── Session ─────────────────────────────────────────────────────────────────

class Session(BaseModel):
    session_id:       str
    user_id:          str
    trunk_branch_id:  str
    active_branch_id: str
    title:            Optional[str] = None
    created_at:       str
    updated_at:       str


class UpdateSessionRequest(BaseModel):
    title:            Optional[str] = None
    active_branch_id: Optional[str] = None


# ─── Paginated responses ──────────────────────────────────────────────────────

class PaginatedMessages(BaseModel):
    items:       list[Message]
    total:       int
    page:        int
    page_size:   int
    has_more:    bool
    next_cursor: Optional[str] = None   # base64-encoded LastEvaluatedKey


class PaginatedSessions(BaseModel):
    items:       list[Session]
    total:       int
    page:        int
    page_size:   int
    has_more:    bool
    next_cursor: Optional[str] = None


# ─── Generic responses ────────────────────────────────────────────────────────

class SuccessResponse(BaseModel):
    success: bool = True
    message: str = "OK"


class ErrorResponse(BaseModel):
    error:   str
    detail:  Optional[str] = None
