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

## Deploy steps

### 1. Package and deploy
```bash
bash deploy.sh chatbot-api us-east-1
```

### 2. Create Lambda (first time only)
```bash
aws lambda create-function \
  --function-name chatbot-api \
  --runtime python3.12 \
  --role arn:aws:iam::YOUR_ACCOUNT:role/chatbot-lambda-role \
  --handler lambda_handler.handler \
  --zip-file fileb://function.zip \
  --timeout 30 \
  --memory-size 256 \
  --environment "Variables={
    COGNITO_USER_POOL_ID=us-east-1_XXXXXXXX,
    COGNITO_CLIENT_ID=XXXXXXXXXX,
    CORS_ORIGINS=https://yourapp.com
  }" \
  --region us-east-1
```

### 3. Create Function URL (BUFFERED mode — NOT streaming)
```bash
aws lambda create-function-url-config \
  --function-name chatbot-api \
  --auth-type NONE \
  --invoke-mode BUFFERED

aws lambda add-permission \
  --function-name chatbot-api \
  --statement-id FunctionURLAllowPublicAccess \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE
```

### 4. Attach DynamoDB policy
Same policy as the streaming Lambda — attach `infra/lambda-iam-policy.json`
from the streaming Lambda project to this Lambda's role as well.

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
