from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from jose.exceptions import ExpiredSignatureError
import httpx
import time
from app.config import get_settings

bearer_scheme = HTTPBearer()

# ─── JWKS cache ──────────────────────────────────────────────────────────────
# Fetched once per cold start, reused across warm invocations.
# Cognito rotates keys rarely — cold-start fetch is fine.

_jwks_cache: dict | None = None
_jwks_fetched_at: float = 0
_JWKS_TTL = 3600  # re-fetch after 1 hour


def _get_jwks(jwks_url: str) -> dict:
    global _jwks_cache, _jwks_fetched_at
    now = time.time()
    if _jwks_cache is None or (now - _jwks_fetched_at) > _JWKS_TTL:
        response = httpx.get(jwks_url, timeout=5)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_fetched_at = now
    return _jwks_cache


def _get_public_key(token: str, jwks: dict):
    """Extract the matching public key from JWKS for this token's kid."""
    headers = jwt.get_unverified_header(token)
    kid = headers.get("kid")
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Public key not found in JWKS"
    )


# ─── Main verifier ───────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    FastAPI dependency. Use as:
        @router.get("/something")
        async def endpoint(user = Depends(get_current_user)):
            user_id = user["sub"]

    Returns the decoded JWT payload on success.
    Raises HTTP 401 on any failure.
    """
    settings = get_settings()
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        jwks = _get_jwks(settings.cognito_jwks_url)
        public_key = _get_public_key(token, jwks)

        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=settings.cognito_client_id,
            options={"verify_at_hash": False},
        )

        # Cognito access tokens use token_use = "access"
        # Cognito id tokens use token_use = "id"
        # Accept both for flexibility
        if payload.get("token_use") not in ("access", "id"):
            raise credentials_exception

        return payload

    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise credentials_exception
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not fetch Cognito JWKS: {str(e)}",
        )
