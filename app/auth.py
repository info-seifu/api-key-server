from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Dict, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt

from .config import Settings, get_settings

logger = logging.getLogger("api-key-server.auth")


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
        # 内部ログには詳細を記録、クライアントには一般的なメッセージ
        logger.warning(f"JWT verification failed: {exc}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

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
        # ログには詳細情報を記録（computed/signatureは含めない - Secret漏洩リスク）
        logger.warning(
            "HMAC signature mismatch",
            extra={
                "client_id": client_id,
                "timestamp": timestamp,
                "method": request.method,
                "path": request.url.path,
            }
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signature mismatch")

    product_id = request.path_params.get("product")
    if not product_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing product in path")

    logger.info(f"HMAC authentication successful for client: {client_id}")

    return AuthContext(user_id=client_id, product_id=str(product_id), method="hmac", client_id=client_id)


def verify_iap_jwt(iap_jwt: str, request: Request, settings: Settings) -> AuthContext:
    """
    Verify Google IAP (Identity-Aware Proxy) JWT token.

    IAP adds the X-Goog-IAP-JWT-Assertion header with a signed JWT.
    The JWT contains user identity information (email, user_id).
    """
    try:
        # Decode without verification first to get the key ID
        unverified_header = jwt.get_unverified_header(iap_jwt)
        unverified_claims = jwt.get_unverified_claims(iap_jwt)

        # IAP JWT should have specific audience format
        # Expected audience: /projects/PROJECT_NUMBER/apps/PROJECT_ID
        expected_audience_prefix = "/projects/"
        audience = unverified_claims.get("aud", "")

        if not audience.startswith(expected_audience_prefix):
            logger.warning(f"Invalid IAP JWT audience format: {audience}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid IAP token audience"
            )

        # Google's public keys are fetched automatically by python-jose
        # For IAP, we verify with Google's public keys
        try:
            # Verify the token using Google's public keys
            # The jose library will fetch Google's public keys from:
            # https://www.gstatic.com/iap/verify/public_key-jwk
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token

            # Verify using Google's library
            claims = id_token.verify_token(
                iap_jwt,
                google_requests.Request(),
                audience=audience,
                certs_url="https://www.gstatic.com/iap/verify/public_key"
            )

        except ImportError:
            # google-auth is required for IAP JWT verification
            logger.error("google-auth not installed - IAP verification disabled")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="IAP verification unavailable"
            )

        # Extract user information from claims
        user_email = claims.get("email")
        user_id = claims.get("sub") or user_email

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="IAP token missing user identity"
            )

        # Get product_id from URL path
        product_id = request.path_params.get("product")
        if not product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing product in path"
            )

        logger.info(f"IAP authentication successful for user: {user_email}")

        return AuthContext(
            user_id=str(user_id),
            product_id=str(product_id),
            method="iap",
            client_id=user_email
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"IAP JWT verification failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid IAP token"
        )


async def ensure_authenticated(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_timestamp: Optional[str] = Header(default=None, alias="X-Timestamp"),
    x_signature: Optional[str] = Header(default=None, alias="X-Signature"),
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
    x_goog_iap_jwt_assertion: Optional[str] = Header(default=None, alias="X-Goog-IAP-JWT-Assertion"),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    # Check for IAP JWT first (highest priority for IAP-protected services)
    if x_goog_iap_jwt_assertion:
        return verify_iap_jwt(x_goog_iap_jwt_assertion, request, settings)

    # Check for standard JWT (Authorization: Bearer)
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        return verify_jwt_token(token, settings)

    # Check for HMAC authentication
    if x_timestamp and x_signature and x_client_id:
        return await verify_hmac(
            client_id=x_client_id,
            timestamp=x_timestamp,
            signature=x_signature,
            request=request,
            settings=settings,
        )

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


async def verify_hmac_multipart(
    *,
    client_id: str,
    timestamp: str,
    signature: str,
    product_id: str,
    method: str,
    path: str,
    form_fields: dict,
    settings: Settings,
) -> AuthContext:
    """
    HMAC authentication for multipart/form-data requests.

    For multipart requests, the body_hash is calculated from form fields (excluding file).
    Client should calculate: SHA256(JSON.stringify({model, language, ...}))

    Args:
        client_id: Client identifier
        timestamp: Unix timestamp string
        signature: HMAC signature
        product_id: Product identifier from URL path
        method: HTTP method
        path: URL path
        form_fields: Form fields excluding file (model, language, etc.)
        settings: Application settings
    """
    import json

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

    # For multipart, body_hash is calculated from form fields (excluding file)
    # Sort keys for consistent hash calculation
    sorted_fields = dict(sorted(form_fields.items()))
    body_bytes = json.dumps(sorted_fields, separators=(",", ":")).encode("utf-8")
    computed = _calculate_hmac_signature(secret, timestamp, method, path, body_bytes)

    if not hmac.compare_digest(computed, signature):
        logger.warning(
            "HMAC signature mismatch (multipart)",
            extra={
                "client_id": client_id,
                "timestamp": timestamp,
                "method": method,
                "path": path,
            }
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signature mismatch")

    logger.info(f"HMAC authentication successful for client: {client_id} (multipart)")

    return AuthContext(user_id=client_id, product_id=product_id, method="hmac", client_id=client_id)
