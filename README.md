# Chatbot Management API

Python FastAPI deployed on AWS Lambda via Mangum. Handles all non-streaming operations: sessions, branches (conversation tree), and message state management.

## Project Structure

```
lambda_handler.py          ← Lambda entry point
app/
  main.py                  ← FastAPI app, CORS, request logging
  config.py                ← Environment variable settings (pydantic-settings)
  middleware/
    auth.py                ← Cognito JWT verification via JWKS
  models/
    schemas.py             ← Pydantic request/response models
  routers/
    sessions.py            ← /sessions endpoints
    branches.py            ← /branches endpoints
    messages.py            ← /messages endpoints
  services/
    dynamodb.py            ← All DynamoDB reads/writes
```

## Environment Variables

| Variable               | Required | Default       | Description                             |
|------------------------|----------|---------------|-----------------------------------------|
| `COGNITO_USER_POOL_ID` | yes      | —             | e.g. `us-east-1_XXXXXXXXX`             |
| `COGNITO_CLIENT_ID`    | yes      | —             | Cognito app client ID                   |
| `COGNITO_REGION`       | no       | `us-east-1`   | Region where the User Pool is hosted    |
| `AWS_REGION`           | no       | `us-east-1`   | Region for DynamoDB                     |
| `CORS_ORIGINS`         | yes      | —             | Comma-separated list of allowed origins |

Copy `.env.example` to `.env` and fill in the required values for local development.

## Local Development

### Prerequisites

- Python 3.12+
- Docker (for SAM)
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)

### Run with SAM

```bash
# Build the Lambda package in a Docker container
sam build --use-container

# Start a local HTTP server on port 8000
sam local start-api --port 8000
```

```bash
curl http://localhost:8000/health
```

### Run with uvicorn (faster for development)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## API Reference

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sessions` | List sessions, most recent first (paginated) |
| `GET` | `/sessions/{session_id}` | Get a session |
| `PATCH` | `/sessions/{session_id}` | Update title or active branch |
| `DELETE` | `/sessions/{session_id}` | Delete session and all its branches and messages |
| `GET` | `/sessions/usage/stats` | Aggregated token usage for the current user |

### Branches

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/branches/session/{session_id}` | List all branches in a session |
| `GET` | `/branches/{branch_id}` | Get a branch |
| `POST` | `/branches/fork` | Fork a new branch from a message selection |

### Messages

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/messages/branch/{branch_id}` | List messages in a branch (paginated) |
| `GET` | `/messages/{msg_id}` | Get a message |
| `PATCH` | `/messages/{msg_id}` | Update state and/or content |
| `DELETE` | `/messages/{msg_id}` | Soft delete (sets state=deleted) |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check — no auth required |

### Pagination

Paginated endpoints (`/sessions`, `/messages/branch/{branch_id}`) accept:
- `page_size` — number of items per page (1–100, default 20)
- `cursor` — opaque token from the previous response's `next_cursor`

### Message States

`PATCH /messages/{msg_id}` accepts a `state` field and optional `content`:

```json
{ "state": "stopped" }
{ "state": "edited", "content": "Updated text" }
{ "state": "deleted" }
{ "state": "active" }
```

## Interactive Docs

FastAPI generates interactive API docs automatically:

```
https://YOUR_FUNCTION_URL/docs      ← Swagger UI
https://YOUR_FUNCTION_URL/redoc     ← ReDoc
```

## Deployment

Deployment is handled automatically by GitHub Actions on every push to `dev`. The workflow:

1. Validates Python syntax and project structure
2. Installs dependencies for the Lambda Linux environment (`manylinux2014_x86_64`)
3. Creates and uploads a deployment zip
4. Runs a smoke test against `/health`
