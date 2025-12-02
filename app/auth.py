from __future__ import annotations

import hashlib
import hmac
import time
from typing import Dict, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt

from .config import Settings, get_settings


class AuthContext:
    def __init__(self, user_id: str, product_id: str, method: str, client_id: Optional[str] = None):
        self.user_id = user_id
        self.product_id = product_id
        self.method = method
        self.client_id = client_id


async def _read_body(request: Request) -> bytes:
    if not hasattr(request.state, "cached_body"):
        request.state.cached_body = await request.body()
    return request.state.cached_body


def _jwt_kid(token: str) -> Optional[str]:
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError:
        return None
    return unverified_header.get("kid")


def _select_public_key(jwt_public_keys: Dict[str, str], kid: Optional[str]) -> Optional[str]:
    if kid and kid in jwt_public_keys:
        return jwt_public_keys[kid]
    if not kid and len(jwt_public_keys) == 1:
        return next(iter(jwt_public_keys.values()))
    return None


def verify_jwt_token(token: str, settings: Settings) -> AuthContext:
    kid = _jwt_kid(token)
    public_key = _select_public_key(settings.jwt_public_keys, kid)
    if not public_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown key id")

    try:
        payload = jwt.decode(token, public_key, algorithms=["RS256"], audience=settings.jwt_audience)
    except JWTError as exc:  # type: ignore[catching-non-exception]
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    user_id = payload.get("sub")
    product_id = payload.get("product")
    if not user_id or not product_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing claims")

    return AuthContext(user_id=str(user_id), product_id=str(product_id), method="jwt")


def _calculate_hmac_signature(secret: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}"
    mac = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


async def verify_hmac(
    *,
    client_id: str,
    timestamp: str,
    signature: str,
    request: Request,
    settings: Settings,
) -> AuthContext:
    if client_id not in settings.client_hmac_secrets:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown client")

    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid timestamp")

    now = int(time.time())
    if abs(now - ts) > settings.hmac_clock_tolerance_seconds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Timestamp outside tolerance")

    secret = settings.client_hmac_secrets[client_id]
    body = await _read_body(request)
    computed = _calculate_hmac_signature(secret, timestamp, request.method, request.url.path, body)
    if not hmac.compare_digest(computed, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signature mismatch")

    product_id = request.path_params.get("product")
    if not product_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing product in path")

    return AuthContext(user_id=client_id, product_id=str(product_id), method="hmac", client_id=client_id)


async def ensure_authenticated(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_timestamp: Optional[str] = Header(default=None, alias="X-Timestamp"),
    x_signature: Optional[str] = Header(default=None, alias="X-Signature"),
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        return verify_jwt_token(token, settings)

    if x_timestamp and x_signature and x_client_id:
        return await verify_hmac(
            client_id=x_client_id,
            timestamp=x_timestamp,
            signature=x_signature,
            request=request,
            settings=settings,
        )

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
