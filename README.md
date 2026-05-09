# Chatbot Management API — FastAPI Lambda

Python FastAPI on Lambda via Mangum. Handles all non-streaming operations:
sessions, branches (conversation tree), and message state management.

## Project structure

```
lambda_handler.py          ← Lambda entry point (imports from app/main.py)
app/
  main.py                  ← FastAPI app + Mangum wrapper + CORS
  config.py                ← All env vars (pydantic-settings)
  middleware/
    auth.py                ← Cognito JWT verification via JWKS
  models/
    schemas.py             ← All Pydantic request/response models
  routers/
    sessions.py            ← GET/PATCH/DELETE /sessions
    branches.py            ← GET /branches, POST /branches/fork
    messages.py            ← GET/PATCH/DELETE /messages
  services/
    dynamodb.py            ← All DynamoDB reads/writes
```

## Local Development

### Prerequisites
- **Docker** — for SAM to build and run your Lambda locally
- **SAM CLI** — `pip install aws-sam-cli`
- **Python 3.12+** — (Docker runs this, not required locally)

### Setup

#### 1. Download SAM template from AWS Lambda
In AWS Lambda console, download the SAM template (YAML file) for your function.

#### 2. Organize code to match template
The downloaded YAML expects code in a `src/` directory. Restructure your project:
```bash
mkdir -p src
mv app src/
mv lambda_handler.py src/
mv requirements.txt src/
```

Result:
```
fast-api-lambda/
├── template.yml
└── src/
    ├── lambda_handler.py
    ├── requirements.txt
    └── app/
        ├── main.py
        ├── config.py
        └── ...
```

#### 3. Add API Gateway for local testing (optional)
To test locally with `sam local start-api` (HTTP server), modify your `template.yml` to match [template.example.yml](./template.example.yml).

**Key changes from downloaded template:**
1. Added `ChatbotApi` resource (API Gateway)
2. Added `Events` section in `fastapilambda` to connect Lambda to API
3. Changed `CodeUri` from `./src` — adjust if your code is elsewhere

### Run Locally

#### Build
```bash
sam build --use-container
```

#### Start API server
```bash
sam local start-api --port 8000
```

Your API is now running at `http://localhost:3000`

#### Test endpoints
```bash
curl http://localhost:8000/health

# View interactive docs
open http://localhost:8000/docs
```

#### Or use `sam local invoke` (without HTTP server)
```bash
sam local invoke fastapilambda -e events/event.json
```

## Environment variables

| Variable                  | Required | Default                  | Description                        |
|---------------------------|----------|--------------------------|------------------------------------|
| `AWS_REGION`              | no       | `us-east-1`              | AWS region                         |
| `COGNITO_USER_POOL_ID`    | yes      | —                        | e.g. `us-east-1_XXXXXXXXX`        |
| `COGNITO_CLIENT_ID`       | yes      | —                        | Cognito app client ID              |
| `COGNITO_REGION`          | no       | `us-east-1`              | Cognito region                     |
| `DYNAMO_MESSAGES_TABLE`   | no       | `chatbot_messages`       | Must match streaming Lambda        |
| `DYNAMO_BRANCHES_TABLE`   | no       | `chatbot_branches`       | Must match streaming Lambda        |
| `DYNAMO_SESSIONS_TABLE`   | no       | `chatbot_sessions`       | Must match streaming Lambda        |
| `CORS_ORIGINS`            | yes      | `https://yourapp.com`    | Comma-separated allowed origins    |
| `DEFAULT_PAGE_SIZE`       | no       | `20`                     | Default pagination size            |
| `MAX_PAGE_SIZE`           | no       | `100`                    | Max pagination size                |


## API Reference

### Sessions

```
GET    /sessions                      List user sessions (paginated)
GET    /sessions/{session_id}         Get single session
PATCH  /sessions/{session_id}         Update title or active branch
DELETE /sessions/{session_id}         Delete session
```

### Branches

```
GET    /branches/session/{session_id} List all branches in a session
GET    /branches/{branch_id}          Get single branch + selectedMsgIds
POST   /branches/fork                 Fork a new branch with custom message selection
```

### Messages

```
GET    /messages/branch/{branch_id}   List messages in a branch (paginated)
GET    /messages/{msg_id}             Get single message
PATCH  /messages/{msg_id}             Stop / edit / delete / restore
DELETE /messages/{msg_id}             Soft delete
```

### Health

```
GET    /health                        Health check (no auth required)
```

## Test with curl

Replace `YOUR_API_URL` and `YOUR_TOKEN` below.

```bash
API=https://YOUR_FUNCTION_URL
TOKEN=your-cognito-access-token

# Health check (no auth needed)
curl $API/health

# List sessions
curl $API/sessions \
  -H "Authorization: Bearer $TOKEN"

# List messages in a branch (paginated)
curl "$API/messages/branch/branch_abc123?page_size=20" \
  -H "Authorization: Bearer $TOKEN"

# Get next page using cursor
curl "$API/messages/branch/branch_abc123?cursor=CURSOR_FROM_PREVIOUS_RESPONSE" \
  -H "Authorization: Bearer $TOKEN"

# Stop a message
curl -X PATCH $API/messages/msg_abc123 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"state": "stopped"}'

# Edit a message
curl -X PATCH $API/messages/msg_abc123 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"state": "edited", "content": "Updated text"}'

# Fork a branch
curl -X POST $API/branches/fork \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session_abc",
    "parent_branch_id": "branch_xyz",
    "parent_msg_id": "msg_123",
    "selected_msg_ids": ["msg_001", "msg_003", "msg_005"],
    "label": "without that tangent"
  }'

# Switch active branch on a session
curl -X PATCH $API/sessions/session_abc \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"active_branch_id": "branch_new"}'
```

## Auto-generated API docs

FastAPI generates interactive docs automatically. Once deployed, visit:
```
https://YOUR_FUNCTION_URL/docs      ← Swagger UI
https://YOUR_FUNCTION_URL/redoc     ← ReDoc
```

These are extremely useful for testing without curl.
