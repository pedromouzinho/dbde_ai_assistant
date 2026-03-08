import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

import auth
from config import JWT_SECRET


@pytest.fixture(autouse=True)
def _reset_auth_state():
    with auth._blacklist_lock:
        auth._token_blacklist.clear()
    with auth._user_invalidated_lock:
        auth._user_invalidated_before.clear()
    with auth._login_attempts_lock:
        auth._login_attempts.clear()
    yield
    with auth._blacklist_lock:
        auth._token_blacklist.clear()
    with auth._user_invalidated_lock:
        auth._user_invalidated_before.clear()
    with auth._login_attempts_lock:
        auth._login_attempts.clear()


class TestJWTClaims:
    """Verifica que jti e iat sao adicionados automaticamente."""

    def test_jwt_encode_adds_jti(self):
        token = auth.jwt_encode({"sub": "test", "role": "user"})
        payload = auth.jwt_decode(token)

        assert "jti" in payload
        assert len(payload["jti"]) == 32
        int(payload["jti"], 16)

    def test_jwt_encode_adds_iat(self):
        token = auth.jwt_encode({"sub": "test", "role": "user"})
        payload = auth.jwt_decode(token)

        assert "iat" in payload
        iat = datetime.fromisoformat(payload["iat"])
        assert iat.tzinfo is not None

    def test_jwt_encode_preserves_custom_jti(self):
        token = auth.jwt_encode({"sub": "test", "jti": "custom123"})
        payload = auth.jwt_decode(token)

        assert payload["jti"] == "custom123"

    def test_jwt_unique_jti_per_token(self):
        token_1 = auth.jwt_encode({"sub": "test"})
        token_2 = auth.jwt_encode({"sub": "test"})
        payload_1 = auth.jwt_decode(token_1)
        payload_2 = auth.jwt_decode(token_2)

        assert payload_1["jti"] != payload_2["jti"]

    def test_old_token_without_jti_still_valid(self):
        exp = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        payload_data = {"sub": "old_user", "role": "user", "exp": exp}
        header = auth._b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        pay = auth._b64url_encode(json.dumps(payload_data).encode())
        sig = auth._b64url_encode(
            hmac.new(JWT_SECRET.encode(), f"{header}.{pay}".encode(), hashlib.sha256).digest()
        )
        old_token = f"{header}.{pay}.{sig}"

        result = auth.jwt_decode(old_token)

        assert result["sub"] == "old_user"


class TestTokenBlacklist:
    """Verifica blacklist de tokens."""

    def test_blacklist_token_then_reject(self):
        token = auth.jwt_encode({"sub": "test"})
        payload = auth.jwt_decode(token)
        exp = datetime.fromisoformat(payload["exp"])

        auth.blacklist_token(payload["jti"], exp)

        with pytest.raises(ValueError, match="revoked"):
            auth.jwt_decode(token)

    def test_non_blacklisted_token_accepted(self):
        token_1 = auth.jwt_encode({"sub": "test"})
        token_2 = auth.jwt_encode({"sub": "test"})
        payload_1 = auth.jwt_decode(token_1)

        auth.blacklist_token(payload_1["jti"], datetime.fromisoformat(payload_1["exp"]))

        payload_2 = auth.jwt_decode(token_2)
        assert payload_2["sub"] == "test"

    def test_cleanup_removes_expired(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        auth.blacklist_token("expired-jti", past)

        assert auth.is_token_blacklisted("expired-jti")

        removed = auth.cleanup_blacklist()

        assert removed >= 1
        assert not auth.is_token_blacklisted("expired-jti")


class TestUserInvalidation:
    """Verifica invalidacao global de tokens por user."""

    def test_invalidate_user_tokens(self):
        token = auth.jwt_encode({"sub": "victim"})

        time.sleep(0.01)
        auth.invalidate_user_tokens("victim")

        with pytest.raises(ValueError, match="invalidated"):
            auth.jwt_decode(token)

    def test_new_token_after_invalidation_works(self):
        auth.invalidate_user_tokens("user_x")

        time.sleep(0.01)
        token = auth.jwt_encode({"sub": "user_x"})
        payload = auth.jwt_decode(token)

        assert payload["sub"] == "user_x"


class TestAccountLockout:
    """Verifica lockout de conta apos tentativas falhadas."""

    def test_not_locked_initially(self):
        assert not auth.is_account_locked("fresh_user_locktest")

    def test_locked_after_max_attempts(self):
        username = "lockout_test_user"
        for _ in range(auth._MAX_LOGIN_ATTEMPTS):
            auth.record_login_failure(username)

        assert auth.is_account_locked(username)

    def test_clear_attempts_unlocks(self):
        username = "clear_test_user"
        for _ in range(auth._MAX_LOGIN_ATTEMPTS):
            auth.record_login_failure(username)

        assert auth.is_account_locked(username)

        auth.clear_login_attempts(username)

        assert not auth.is_account_locked(username)
