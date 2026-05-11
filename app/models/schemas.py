from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal


# ─── Enums ───────────────────────────────────────────────────────────────────

MessageRole  = Literal["user", "assistant"]
MessageState = Literal["active", "stopped", "edited", "deleted"]


# ─── Message ─────────────────────────────────────────────────────────────────

class Message(BaseModel):
    msg_id:       str
    session_id:   str
    branch_id:    str
    role:         MessageRole
    content:      Optional[str] = None
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

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.state is None and self.content is None:
            raise ValueError("At least one of 'state' or 'content' must be provided")
        if self.state == "edited" and not self.content:
            raise ValueError("content is required when state is 'edited'")
        return self

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

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.title is None and self.active_branch_id is None:
            raise ValueError("At least one of 'title' or 'active_branch_id' must be provided")
        return self


# ─── Paginated responses ──────────────────────────────────────────────────────

class PaginatedMessages(BaseModel):
    items:       list[Message]
    count:       int
    page:        int
    page_size:   int
    has_more:    bool
    next_cursor: Optional[str] = None   # base64-encoded LastEvaluatedKey


class PaginatedSessions(BaseModel):
    items:       list[Session]
    count:       int
    page:        int
    page_size:   int
    has_more:    bool
    next_cursor: Optional[str] = None


# ─── Usage Stats ─────────────────────────────────────────────────────────────

class DailyUsage(BaseModel):
    date:          str
    input_tokens:  int
    output_tokens: int
    message_count: int


class ModelBreakdown(BaseModel):
    model_id:    str
    token_count: int
    percentage:  int


class UsageStats(BaseModel):
    total_messages:      int
    total_input_tokens:  int
    total_output_tokens: int
    total_tokens:        int
    estimated_cost_usd:  float
    daily_usage:         list[DailyUsage]
    model_breakdown:     list[ModelBreakdown]


# ─── Generic responses ────────────────────────────────────────────────────────

class SuccessResponse(BaseModel):
    success: bool = True
    message: str = "OK"


class ErrorResponse(BaseModel):
    error:   str
    detail:  Optional[str] = None
