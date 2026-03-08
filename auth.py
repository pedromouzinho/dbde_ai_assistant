# =============================================================================
# auth.py — Autenticação JWT e gestão de passwords v7.0
# =============================================================================
# Zero dependências externas — usa hmac e hashlib da stdlib.
# =============================================================================

import json
import base64
import secrets
import logging
import threading
import time
import uuid
import hmac as _hmac
import hashlib as _hashlib
from contextvars import ContextVar, Token
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import JWT_SECRET, JWT_SECRET_PREVIOUS, JWT_EXPIRATION_HOURS, AUTH_COOKIE_NAME
logger = logging.getLogger(__name__)
_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_DURATION_MINUTES = 15

# Token blacklist — in-memory, auto-cleanup de tokens expirados
_token_blacklist: dict[str, datetime] = {}
_blacklist_lock = threading.Lock()

# User-level invalidation — todos os tokens emitidos antes deste timestamp sao invalidos
_user_invalidated_before: dict[str, datetime] = {}
_user_invalidated_lock = threading.Lock()

_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = threading.Lock()


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
    if "iat" not in data:
        data["iat"] = datetime.now(timezone.utc).isoformat()
    if "jti" not in data:
        data["jti"] = uuid.uuid4().hex
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    pay = _b64url_encode(json.dumps(data, default=str).encode())
    sig_input = f"{header}.{pay}".encode()
    sig = _b64url_encode(_hmac.new(secret.encode(), sig_input, _hashlib.sha256).digest())
    return f"{header}.{pay}.{sig}"


def jwt_decode(token: str, secret: str = JWT_SECRET) -> dict:
    """Decode JWT. Tenta secret actual; se falhar e houver previous, tenta esse."""
    try:
        return _jwt_decode_single(token, secret)
    except ValueError as e:
        if JWT_SECRET_PREVIOUS and secret == JWT_SECRET:
            try:
                return _jwt_decode_single(token, JWT_SECRET_PREVIOUS)
            except ValueError:
                pass
        raise ValueError(f"JWT decode error: {e}")


def _jwt_decode_single(token: str, secret: str) -> dict:
    """Decode JWT com um único secret. Raises ValueError se falhar."""
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

    jti = payload.get("jti")
    if jti and is_token_blacklisted(str(jti)):
        raise ValueError("Token revoked")

    sub = str(payload.get("sub", "") or "")
    iat_raw = payload.get("iat")
    if sub and iat_raw and isinstance(iat_raw, str):
        iat = datetime.fromisoformat(iat_raw)
        if iat.tzinfo is None:
            iat = iat.replace(tzinfo=timezone.utc)
        if is_user_token_invalidated(sub, iat):
            raise ValueError("Token invalidated by admin")
    return payload


def blacklist_token(jti: str, exp: datetime) -> None:
    """Adiciona um token (por jti) a blacklist ate expirar."""
    if not jti:
        return
    expiry = exp
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    with _blacklist_lock:
        _token_blacklist[jti] = expiry


def is_token_blacklisted(jti: str) -> bool:
    """Verifica se um token esta na blacklist."""
    if not jti:
        return False
    with _blacklist_lock:
        return jti in _token_blacklist


def invalidate_user_tokens(username: str) -> None:
    """Invalida todos os tokens de um user emitidos antes de agora."""
    if not username:
        return
    with _user_invalidated_lock:
        _user_invalidated_before[username] = datetime.now(timezone.utc)


def is_user_token_invalidated(username: str, iat: datetime) -> bool:
    """Verifica se o token de um user foi invalidado globalmente."""
    if not username:
        return False
    issued_at = iat
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    with _user_invalidated_lock:
        cutoff = _user_invalidated_before.get(username)
    if cutoff is None:
        return False
    return issued_at <= cutoff


def cleanup_blacklist() -> int:
    """Remove tokens expirados da blacklist. Retorna numero de removidos."""
    now = datetime.now(timezone.utc)
    removed = 0
    with _blacklist_lock:
        expired_jtis = [jti for jti, exp in _token_blacklist.items() if now > exp]
        for jti in expired_jtis:
            del _token_blacklist[jti]
            removed += 1
    return removed


def record_login_failure(username: str) -> None:
    """Regista uma tentativa falhada de login."""
    if not username:
        return
    now = time.time()
    with _login_attempts_lock:
        attempts = _login_attempts.setdefault(username, [])
        attempts.append(now)
        cutoff = now - (_LOCKOUT_DURATION_MINUTES * 60)
        _login_attempts[username] = [t for t in attempts if t > cutoff]


def is_account_locked(username: str) -> bool:
    """Verifica se a conta esta bloqueada por tentativas falhadas."""
    if not username:
        return False
    now = time.time()
    cutoff = now - (_LOCKOUT_DURATION_MINUTES * 60)
    with _login_attempts_lock:
        attempts = _login_attempts.get(username, [])
        recent = [t for t in attempts if t > cutoff]
        _login_attempts[username] = recent
        return len(recent) >= _MAX_LOGIN_ATTEMPTS


def clear_login_attempts(username: str) -> None:
    """Limpa tentativas falhadas apos login bem sucedido."""
    if not username:
        return
    with _login_attempts_lock:
        _login_attempts.pop(username, None)


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
