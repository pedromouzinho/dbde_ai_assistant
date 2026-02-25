# =============================================================================
# auth.py — Autenticação JWT e gestão de passwords v7.0
# =============================================================================
# Zero dependências externas — usa hmac e hashlib da stdlib.
# =============================================================================

import json
import base64
import secrets
import logging
import hmac as _hmac
import hashlib as _hashlib
from contextvars import ContextVar, Token
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import JWT_SECRET, JWT_EXPIRATION_HOURS, AUTH_COOKIE_NAME
logger = logging.getLogger(__name__)


# =============================================================================
# BASE64 URL-SAFE ENCODING
# =============================================================================

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _b64url_decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


# =============================================================================
# JWT
# =============================================================================

def jwt_encode(payload: dict, secret: str = JWT_SECRET) -> str:
    data = dict(payload or {})
    if "exp" not in data:
        data["exp"] = (datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS)).isoformat()
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    pay = _b64url_encode(json.dumps(data, default=str).encode())
    sig_input = f"{header}.{pay}".encode()
    sig = _b64url_encode(_hmac.new(secret.encode(), sig_input, _hashlib.sha256).digest())
    return f"{header}.{pay}.{sig}"


def jwt_decode(token: str, secret: str = JWT_SECRET) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid token format")
        header_b64, payload_b64, sig_b64 = parts
        expected_sig = _b64url_encode(
            _hmac.new(secret.encode(), f"{header_b64}.{payload_b64}".encode(), _hashlib.sha256).digest()
        )
        if not _hmac.compare_digest(sig_b64, expected_sig):
            raise ValueError("Invalid signature")
        payload = json.loads(_b64url_decode(payload_b64))
        if "exp" not in payload:
            raise ValueError("Token missing exp")
        exp_raw = payload["exp"]
        if not isinstance(exp_raw, str):
            raise ValueError("Token exp inválido")
        exp = datetime.fromisoformat(exp_raw)
        if exp.tzinfo is None:
            # Compat: tokens antigos podem ter timestamps naive.
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp:
            raise ValueError("Token expired")
        return payload
    except (ValueError, json.JSONDecodeError, Exception) as e:
        raise ValueError(f"JWT decode error: {e}")


# =============================================================================
# PASSWORD HASHING
# =============================================================================

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = _hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"pbkdf2:sha256:100000${salt}${key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        parts = stored_hash.split("$")
        if len(parts) != 3 or not parts[0].startswith("pbkdf2:"):
            return False
        salt = parts[1]
        stored_key = parts[2]
        computed_key = _hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return _hmac.compare_digest(computed_key.hex(), stored_key)
    except Exception as e:
        logger.warning("[Auth] verify_password exception: %s", e)
        return False


# =============================================================================
# FASTAPI DEPENDENCY
# =============================================================================

security = HTTPBearer(auto_error=False)
_request_cookie_token_ctx: ContextVar[str] = ContextVar("dbde_request_cookie_token", default="")


def set_request_cookie_token(token: str) -> Token:
    return _request_cookie_token_ctx.set(token or "")


def reset_request_cookie_token(token_ref: Token) -> None:
    _request_cookie_token_ctx.reset(token_ref)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    request: Optional[Request] = None,
) -> dict:
    """FastAPI dependency — extrai user do JWT token."""
    token = ""
    if request is not None:
        token = (request.cookies.get(AUTH_COOKIE_NAME) or "").strip()
    if not token:
        token = (_request_cookie_token_ctx.get("") or "").strip()
    if not token and credentials is not None:
        token = (credentials.credentials or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token de autenticação em falta")
    try:
        payload = jwt_decode(token)
        return payload
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Token inválido ou expirado: {e}")
