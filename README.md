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

| Variable               | Required | Default     | Description                             |
|------------------------|----------|-------------|-----------------------------------------|
| `COGNITO_USER_POOL_ID` | yes      | —           | e.g. `us-east-1_XXXXXXXXX`             |
| `COGNITO_CLIENT_ID`    | yes      | —           | Cognito app client ID                   |
| `COGNITO_REGION`       | no       | `us-east-1` | Region where the User Pool is hosted    |
| `AWS_REGION`           | no       | `us-east-1` | Region for DynamoDB                     |
| `CORS_ORIGINS`         | no       | `http://localhost:3000` | Comma-separated allowed origins |

## Local Development

### First-time setup

**Prerequisites:** Python 3.12

1. Clone the repo and enter the project directory.

2. Copy the example env file and fill in the required values:

   ```powershell
   cp .env.example .env
   ```

   Open `.env` and set at minimum:
   ```
   COGNITO_USER_POOL_ID=us-east-1_XXXXXXXXX
   COGNITO_CLIENT_ID=your-app-client-id
   ```

3. Create and activate a virtual environment:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   On macOS/Linux:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

4. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   pip install uvicorn boto3
   ```

5. Run the server:

   ```powershell
   uvicorn app.main:app --reload --port 8000
   ```

   The API is now available at `http://localhost:8000`.  
   Interactive docs: `http://localhost:8000/docs`

---

### Running (already set up)

```powershell
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

On macOS/Linux:
```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

---

## API Reference

### Sessions

| Method   | Path                          | Description                                          |
|----------|-------------------------------|------------------------------------------------------|
| `GET`    | `/sessions`                   | List sessions, most recent first (paginated)         |
| `GET`    | `/sessions/{session_id}`      | Get a session                                        |
| `PATCH`  | `/sessions/{session_id}`      | Update title or active branch                        |
| `DELETE` | `/sessions/{session_id}`      | Delete session and all its branches and messages     |
| `GET`    | `/sessions/usage/stats`       | Aggregated token usage for the current user          |

### Branches

| Method | Path                              | Description                              |
|--------|-----------------------------------|------------------------------------------|
| `GET`  | `/branches/session/{session_id}`  | List all branches in a session           |
| `GET`  | `/branches/{branch_id}`           | Get a branch                             |
| `POST` | `/branches/fork`                  | Fork a new branch from a message         |

### Messages

| Method   | Path                          | Description                              |
|----------|-------------------------------|------------------------------------------|
| `GET`    | `/messages/branch/{branch_id}`| List messages in a branch (paginated)    |
| `GET`    | `/messages/{msg_id}`          | Get a message                            |
| `PATCH`  | `/messages/{msg_id}`          | Update state and/or content              |
| `DELETE` | `/messages/{msg_id}`          | Soft delete (sets state=deleted)         |

### Health

| Method | Path      | Description                    |
|--------|-----------|--------------------------------|
| `GET`  | `/health` | Health check — no auth required |

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

## Deployment

Deployment is handled automatically by GitHub Actions on every push to `dev`. The workflow:

1. Validates Python syntax and project structure
2. Installs dependencies for the Lambda Linux environment (`manylinux2014_x86_64`)
3. Creates and uploads a deployment zip
4. Runs a smoke test against `/health`

Production docs:
```
https://YOUR_FUNCTION_URL/docs      ← Swagger UI
https://YOUR_FUNCTION_URL/redoc     ← ReDoc
```
