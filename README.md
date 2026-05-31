# K-AI — REST API

> FastAPI backend for [K-AI](https://github.com/fareedzafar657/k-ai), the branching AI chat interface. Handles sessions, conversation branches, message state, context compaction, and usage stats — all deployed serverlessly on AWS Lambda.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)
![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-FF9900?logo=amazonaws)

---

## Features

- **Session management** — create, list, update, and delete chat sessions with cursor-based pagination
- **Branching conversations** — fork branches from any message, reconstruct the conversation tree on the frontend
- **Cherry-pick** — copy individual messages from any branch into the current one
- **Context compaction** — summarise selected messages using Anthropic, Google Gemini, or AWS Bedrock; revert compactions at any time
- **Message state machine** — messages move through `active → stopped / edited / deleted / compacted` states; soft deletes preserve history
- **Usage stats** — per-user token aggregation with daily breakdown and per-model cost estimates
- **Cognito JWT auth** — JWKS-based RS256 verification with in-process key caching (1 h TTL)
- **Serverless** — Mangum adapter wraps FastAPI for AWS Lambda Function URLs; zero infra to manage
- **CI/CD** — GitHub Actions pipeline validates, packages for Linux, deploys, and smoke-tests on every push to `dev`

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI 0.115 |
| Lambda adapter | Mangum 0.17 |
| Validation | Pydantic v2 + pydantic-settings |
| Auth | AWS Cognito (RS256 via PyJWT + JWKS) |
| HTTP client | httpx |
| Database | AWS DynamoDB (boto3) |
| AI (compaction) | AWS Bedrock Nova Pro · Anthropic API · Google Gemini |
| Local dev server | Uvicorn |
| CI/CD | GitHub Actions |

---

## Quick Start

### Prerequisites

- Python 3.12
- An AWS account with:
  - A **Cognito User Pool** and App Client
  - Three **DynamoDB tables** (see [DynamoDB Setup](#dynamodb-setup))
  - A **Lambda function** (for deployment)

### 1. Clone and set up a virtual environment

```bash
git clone https://github.com/fareedzafar657/chatbot-fast-api-lambda.git
cd chatbot-fast-api-lambda
```

```bash
# Windows
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1

# macOS / Linux
python3.12 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```env
# Required
COGNITO_USER_POOL_ID=us-east-1_XXXXXXXXX
COGNITO_CLIENT_ID=your-app-client-id

# Optional — defaults shown
AWS_REGION=us-east-1
```

### 4. Run the development server

```bash
uvicorn app.main:app --reload --port 8000
```

| URL | Description |
|---|---|
| `http://localhost:8000` | API root |
| `http://localhost:8000/docs` | Swagger UI (interactive) |
| `http://localhost:8000/redoc` | ReDoc |
| `http://localhost:8000/health` | Health check |

---

## Project Structure

```
lambda_handler.py          ← Lambda entry point (re-exports Mangum handler)
app/
├── main.py                ← FastAPI app, CORS, request logging, global error handler
├── config.py              ← Pydantic-settings config (validated at startup)
├── middleware/
│   └── auth.py            ← Cognito JWKS fetch + RS256 JWT verification + ID-token email extraction
├── models/
│   └── schemas.py         ← All Pydantic request/response models
├── routers/
│   ├── sessions.py        ← /sessions endpoints
│   ├── branches.py        ← /branches endpoints
│   └── messages.py        ← /messages endpoints
└── services/
    ├── db_base.py         ← DynamoDB client, table/GSI constants, shared helpers, db→dict converters
    ├── db_sessions.py     ← Session CRUD
    ├── db_branches.py     ← Branch CRUD, fork, cherry-pick
    ├── db_messages.py     ← Message CRUD, usage stats
    └── db_compaction.py   ← Context compaction (AI summarization) and revert
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `COGNITO_USER_POOL_ID` | Yes | — | Format: `us-east-1_XXXXXXXXX` |
| `COGNITO_CLIENT_ID` | Yes | — | Cognito App Client ID |
| `AWS_REGION` | No | `us-east-1` | Region for DynamoDB, Bedrock, and Cognito |
| `DEMO_MODELS_ALLOWED_EMAILS` | No | `""` | Comma-separated emails permitted to use non-default Bedrock models for compaction |

Required variables are validated at startup — the process will refuse to start with a clear error message if they are missing or malformed.

---

## DynamoDB Setup

Create three tables in DynamoDB with the following keys and GSIs.

### `chatbot_sessions`

| Attribute | Type | Role |
|---|---|---|
| `sessionId` | String | Partition key |

**GSIs:**

| Index name | Partition key | Sort key |
|---|---|---|
| `userId-updatedAt-index` | `userId` (S) | `updatedAt` (S) |

### `chatbot_branches`

| Attribute | Type | Role |
|---|---|---|
| `branchId` | String | Partition key |

**GSIs:**

| Index name | Partition key | Sort key |
|---|---|---|
| `sessionId-createdAt-index` | `sessionId` (S) | `createdAt` (S) |

### `chatbot_messages`

| Attribute | Type | Role |
|---|---|---|
| `msgId` | String | Partition key |

**GSIs:**

| Index name | Partition key | Sort key |
|---|---|---|
| `branchId-createdAt-index` | `branchId` (S) | `createdAt` (S) |
| `sessionId-createdAt-index` | `sessionId` (S) | `createdAt` (S) |
| `userId-createdAt-index` | `userId` (S) | `createdAt` (S) |

---

## API Reference

All endpoints (except `/health`) require a `Bearer` token from AWS Cognito in the `Authorization` header.

### Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/sessions` | List sessions, most recent first (paginated) |
| `GET` | `/sessions/{session_id}` | Get a session by ID |
| `PATCH` | `/sessions/{session_id}` | Update title or active branch |
| `DELETE` | `/sessions/{session_id}` | Delete session and all its branches and messages |
| `GET` | `/sessions/usage/stats` | Aggregated token usage and cost for the current user |

### Branches

| Method | Path | Description |
|---|---|---|
| `GET` | `/branches/session/{session_id}` | List all branches in a session (flat list) |
| `GET` | `/branches/{branch_id}` | Get a branch including its `selectedMsgIds` |
| `POST` | `/branches/fork` | Fork a new branch from a selection of messages |
| `POST` | `/branches/{branch_id}/cherry-pick` | Append messages from another branch |
| `POST` | `/branches/{branch_id}/compact` | Summarise selected messages into a single compaction |
| `DELETE` | `/branches/{branch_id}/compact/{summary_msg_id}` | Revert a compaction, restoring original messages |

### Messages

| Method | Path | Description |
|---|---|---|
| `GET` | `/messages/branch/{branch_id}` | List messages in a branch (paginated); pass `include_deleted=true` to include soft-deleted messages |
| `PATCH` | `/messages/{msg_id}` | Update state and/or content |

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check — no auth required |

---

## Key Concepts

### Pagination

Paginated endpoints (`GET /sessions`, `GET /messages/branch/{branch_id}`) accept:

| Query param | Default | Description |
|---|---|---|
| `page_size` | `20` | Items per page (1–100) |
| `cursor` | `null` | Opaque token from the previous response's `next_cursor` |

Response shape:

```json
{
  "items": [...],
  "count": 20,
  "page_size": 20,
  "has_more": true,
  "next_cursor": "<base64-encoded key>"
}
```

Pass `next_cursor` back as `cursor` on the next call. `has_more: false` means you've reached the end.

### Message States

`PATCH /messages/{msg_id}` accepts `state` and optionally `content`:

```json
{ "state": "stopped" }
{ "state": "edited", "content": "Corrected text" }
{ "state": "deleted" }
{ "state": "active" }
```

`content` is only valid when `state` is `"edited"`. Deleted messages are soft-deleted — the record is preserved and can be restored by setting `state` back to `"active"`.

### Context Compaction

`POST /branches/{branch_id}/compact` replaces a selected range of messages with an AI-generated summary, freeing context-window tokens for long conversations.

```json
{
  "msg_ids": ["msg_001", "msg_002", "msg_003"],
  "name": "Early setup discussion",
  "provider": "anthropic",
  "model": "claude-haiku-4-5-20251001",
  "api_key": "sk-ant-..."
}
```

| `provider` | `model` | `api_key` | Notes |
|---|---|---|---|
| `"anthropic"` | Any Claude model ID | Anthropic API key | Calls `api.anthropic.com` |
| `"gemini"` | Any Gemini model ID | Google AI API key | Calls `generativelanguage.googleapis.com` |
| omitted | omitted | — | AWS Bedrock **Nova Pro** (default, no key needed) |
| omitted | A Bedrock model ID | — | AWS Bedrock with a specific model — only permitted for emails listed in `DEMO_MODELS_ALLOWED_EMAILS`; requires `X-Id-Token` header (Cognito ID token) for email verification |

The compacted originals move to `state=compacted`. Use `DELETE /branches/{branch_id}/compact/{summary_msg_id}` to revert and restore them.

> **Note:** when forking a branch that contains a compaction summary, the duplicate summary's `originalMsgIds` is cleared so that deleting the copy does not affect the original branch's compacted messages.

### Branch Forking

`POST /branches/fork` duplicates the selected messages into a new branch, preserving the originals intact. Token counts are cleared on duplicates so usage stats stay accurate.

```json
{
  "session_id": "session_abc",
  "parent_branch_id": "branch_xyz",
  "parent_msg_id": "msg_123",
  "selected_msg_ids": ["msg_001", "msg_003"],
  "label": "without that tangent"
}
```

---

## Deployment

### GitHub Actions (recommended)

The CI/CD pipeline at `.github/workflows/deploy.yml` triggers on every push to `dev`:

1. **Test** — validates Python syntax and checks all required files exist
2. **Deploy** — installs deps for `manylinux2014_x86_64`, creates a deployment zip, uploads to Lambda, waits for the update, and runs a `/health` smoke test

Required GitHub Secrets:

| Secret | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM key with `lambda:UpdateFunctionCode` permission |
| `AWS_SECRET_ACCESS_KEY` | Corresponding secret |

### Lambda configuration checklist

- **Runtime:** Python 3.12
- **Handler:** `lambda_handler.handler`
- **Memory:** 256 MB (recommended)
- **Timeout:** 60 s
- **Architecture:** x86\_64
- **Environment variables:** set all required vars (see [Environment Variables](#environment-variables))
- **IAM role:** must have `dynamodb:*` on the three tables and `bedrock:InvokeModel` on any Bedrock model IDs you intend to use for compaction (at minimum Nova Pro)

---

## Frontend & Streaming Lambda

This service is one of three components that make up K-AI:

| Service | Repo | Purpose |
|---|---|---|
| Frontend | [k-ai](https://github.com/fareedzafar657/k-ai) | Next.js chat UI |
| REST API | **this repo** | Sessions, branches, messages, usage stats |
| Streaming Lambda | [chatbot-streaming-lambda](https://github.com/fareedzafar657/chatbot-streaming-lambda) | Real-time token streaming (NDJSON over Lambda Function URL response streaming) |

---

## License

[MIT](./LICENSE) © Fareed Z.
