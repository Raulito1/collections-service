import json
import logging
import httpx, jwt
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer
from urllib.parse import urlparse

from .config import settings

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=True)
_JWKS: Optional[Dict[str, Any]] = None
_JWKS_FETCHED_AT: Optional[datetime] = None
_JWKS_TTL = timedelta(minutes=10)  # simple refresh window

def _iss() -> str:
    """Return the Supabase issuer (…/auth/v1) derived from the JWKS URL."""
    url = settings.SUPABASE_JWKS_URL
    marker = "/auth/v1"
    if marker in url:
        base = url.split(marker, 1)[0]
        return f"{base}{marker}"
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

async def _fetch_jwks() -> Dict[str, Any]:
    logger.debug("Fetching Supabase JWKS from %s", settings.SUPABASE_JWKS_URL)
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"apikey": settings.SUPABASE_ANON_KEY}
        r = await client.get(settings.SUPABASE_JWKS_URL, headers=headers)
        r.raise_for_status()
        data = r.json()
    logger.debug("Fetched Supabase JWKS payload (%d keys)", len(data.get("keys", [])))
    return data

async def get_jwks(force: bool = False) -> Dict[str, Any]:
    global _JWKS, _JWKS_FETCHED_AT
    needs_refresh = (
        force
        or _JWKS is None
        or _JWKS_FETCHED_AT is None
        or (datetime.utcnow() - _JWKS_FETCHED_AT) > _JWKS_TTL
    )
    if needs_refresh:
        logger.info("Refreshing Supabase JWKS cache (force=%s)", force)
        _JWKS = await _fetch_jwks()
        _JWKS_FETCHED_AT = datetime.utcnow()
    else:
        age = (datetime.utcnow() - _JWKS_FETCHED_AT).total_seconds() if _JWKS_FETCHED_AT else 0.0
        logger.debug("Using cached Supabase JWKS (age=%.1fs)", age)
    return _JWKS

def _public_key_from_kid(jwks: Dict[str, Any], kid: str) -> tuple[Optional[Any], Optional[str]]:
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key:
        logger.warning("JWKS key not found for kid=%s", kid)
        return None, None

    alg = key.get("alg")
    if not alg:
        kty = key.get("kty")
        if kty == "RSA":
            alg = "RS256"
        elif kty == "EC":
            alg = "ES256"

    if not alg:
        logger.warning("Unable to infer JWT algorithm for kid=%s", kid)
        return None, None

    algorithm = jwt.algorithms.get_default_algorithms().get(alg)
    if not algorithm:
        logger.warning("Unsupported JWT algorithm '%s' for kid=%s", alg, kid)
        return None, None

    logger.debug("Resolved JWKS key for kid=%s using alg=%s", kid, alg)
    # PyJWT accepts a JSON string for from_jwk; passing str is safest across versions
    return algorithm.from_jwk(json.dumps(key)), alg

async def get_current_user(token=Depends(bearer)):
    # 1) parse unverified header to get kid
    try:
        unverified_header = jwt.get_unverified_header(token.credentials)
        kid = unverified_header.get("kid")
        alg = unverified_header.get("alg")
        logger.debug("Parsed Supabase JWT header kid=%s alg=%s", kid, alg)
    except Exception:
        logger.warning("Failed to parse Supabase JWT header")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    public_key: Optional[Any] = None

    if alg and alg.startswith("HS"):
        # Supabase default access tokens are signed with the JWT secret using HS256.
        secret = settings.SUPABASE_JWT_SECRET
        if not secret:
            logger.error("SUPABASE_JWT_SECRET is not configured but HS-signed token was provided")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        public_key = secret
    else:
        # 2) get key, retry once on miss (handles rotation)
        jwks = await get_jwks()
        public_key, alg_from_jwks = _public_key_from_kid(jwks, kid or "")
        if public_key is None or alg_from_jwks is None:
            logger.info("JWKS cache miss for kid=%s; forcing refresh", kid)
            jwks = await get_jwks(force=True)
            public_key, alg_from_jwks = _public_key_from_kid(jwks, kid or "")
            if public_key is None or alg_from_jwks is None:
                logger.error("Unable to resolve JWKS key for kid=%s after refresh", kid)
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        if not alg:
            alg = alg_from_jwks

    # 3) verify (exp/nbf/signature on by default). We disable aud; we DO verify issuer.
    try:
        payload = jwt.decode(
            token.credentials,
            public_key,
            algorithms=[alg],
            issuer=_iss(),            # verify iss matches your Supabase project
            options={"verify_aud": False},
        )
        logger.debug("Supabase JWT validated for subject=%s", payload.get("sub"))
        return payload  # includes "sub", "email", etc.
    except jwt.ExpiredSignatureError:
        logger.warning("Supabase JWT expired for kid=%s", kid)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except Exception:
        # On generic failure (e.g., rotated keys not yet fetched), try one more JWKS refresh
        if alg and alg.startswith("HS"):
            logger.warning("Supabase JWT verification failed for kid=%s using HS secret", kid)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        logger.warning("Supabase JWT verification failed for kid=%s; retrying with forced JWKS refresh", kid)
        jwks = await get_jwks(force=True)
        public_key, alg = _public_key_from_kid(jwks, kid or "")
        if public_key is None or alg is None:
            logger.error("Unable to resolve JWKS key for kid=%s after forced refresh", kid)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        try:
            payload = jwt.decode(
                token.credentials,
                public_key,
                algorithms=[alg],
                issuer=_iss(),
                options={"verify_aud": False},
            )
            logger.debug("Supabase JWT validated after JWKS refresh for subject=%s", payload.get("sub"))
            return payload
        except Exception:
            logger.warning("Supabase JWT verification failed after JWKS refresh for kid=%s", kid)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
