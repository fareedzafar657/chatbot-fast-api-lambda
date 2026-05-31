import asyncio
import logging
import time

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt.exceptions import ExpiredSignatureError, PyJWTError
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer()

# ─── JWKS cache ──────────────────────────────────────────────────────────────

_jwks_cache: dict | None = None
_jwks_fetched_at: float = 0
_JWKS_TTL = 3600
_jwks_lock = asyncio.Lock()


async def _get_jwks(jwks_url: str) -> dict:
    global _jwks_cache, _jwks_fetched_at
    if _jwks_cache is not None and (time.time() - _jwks_fetched_at) <= _JWKS_TTL:
        return _jwks_cache
    async with _jwks_lock:
        if _jwks_cache is None or (time.time() - _jwks_fetched_at) > _JWKS_TTL:
            async with httpx.AsyncClient() as client:
                response = await client.get(jwks_url, timeout=5)
                response.raise_for_status()
                _jwks_cache = response.json()
                _jwks_fetched_at = time.time()
    return _jwks_cache


def _get_public_key(token: str, jwks: dict):
    headers = jwt.get_unverified_header(token)
    kid = headers.get("kid")
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
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
        jwks = await _get_jwks(settings.cognito_jwks_url)
        public_key = _get_public_key(token, jwks)

        # Decode without audience verification — Cognito access tokens use
        # `client_id` instead of `aud`, so PyJWT's built-in aud check fails.
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )

        token_use = payload.get("token_use")
        if token_use == "access":
            if payload.get("client_id") != settings.cognito_client_id:
                logger.warning("Token client_id mismatch")
                raise credentials_exception
        else:
            logger.warning(f"Rejected token: token_use={token_use!r} (expected 'access')")
            raise credentials_exception

        return payload

    except ExpiredSignatureError:
        logger.warning("Token validation failed: token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except PyJWTError:
        logger.warning("JWT validation failed")
        raise credentials_exception
    except httpx.HTTPError as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        logger.error(
            f"Failed to fetch Cognito JWKS (upstream_status={status_code}): {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )


# ─── Email extraction (optional, for demo-model gating) ──────────────────────

async def extract_email_from_id_token(
    x_id_token: Optional[str] = Header(default=None, alias="x-id-token"),
) -> Optional[str]:
    """
    FastAPI dependency. Verifies the X-Id-Token header (Cognito ID token) and
    returns the user's email. Returns None if the header is absent or invalid.
    Never raises — callers use the email only for gating, not for auth.
    """
    if not x_id_token:
        return None
    settings = get_settings()
    try:
        jwks = await _get_jwks(settings.cognito_jwks_url)
        public_key = _get_public_key(x_id_token, jwks)
        payload = jwt.decode(
            x_id_token,
            public_key,
            algorithms=["RS256"],
            audience=settings.cognito_client_id,
        )
        return payload.get("email")
    except Exception:
        # Malformed or expired ID token — treat as no email, don't block the request
        return None
