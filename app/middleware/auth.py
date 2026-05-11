import logging
import time

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt.exceptions import ExpiredSignatureError, PyJWTError

from app.config import get_settings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer()
settings = get_settings()

# ─── JWKS cache ──────────────────────────────────────────────────────────────

_jwks_cache: dict | None = None
_jwks_fetched_at: float = 0
_JWKS_TTL = 3600  # re-fetch after 1 hour


async def _get_jwks(jwks_url: str) -> dict:
    global _jwks_cache, _jwks_fetched_at
    now = time.time()
    if _jwks_cache is None or (now - _jwks_fetched_at) > _JWKS_TTL:
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url, timeout=5)
            response.raise_for_status()
            _jwks_cache = response.json()
            _jwks_fetched_at = now
    return _jwks_cache


def _get_public_key(token: str, jwks: dict):
    """Extract and construct the RSA public key matching this token's kid."""
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
        # We verify the client manually below based on token_use.
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )

        token_use = payload.get("token_use")
        if token_use == "access":
            if payload.get("client_id") != settings.cognito_client_id:
                raise credentials_exception
        elif token_use == "id":
            if payload.get("aud") != settings.cognito_client_id:
                raise credentials_exception
        else:
            raise credentials_exception

        return payload

    except ExpiredSignatureError:
        logger.warning("Token validation failed: token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except PyJWTError as e:
        logger.warning(f"JWT validation failed: {str(e)}", extra={"token_prefix": token[:20]})
        raise credentials_exception
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch Cognito JWKS: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )
