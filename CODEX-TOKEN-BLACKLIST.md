# Codex Task: Token Blacklist + Auth Hardening — SWOT W6

**Branch**: create `codex/token-blacklist` from `main`
**Priority**: MEDIO — tokens validos 10h apos logout
**Scope**: auth.py, app.py, config.py, tests/

---

## Contexto

Actualmente, o logout apenas limpa o cookie HTTP — o JWT continua valido
ate expirar (10h). Um atacante que capture o token (XSS, network sniffing,
log leak) pode usa-lo durante todo esse periodo. A solucao e manter uma
**blacklist in-memory** de tokens revogados (por `jti`) e verifica-la no
`jwt_decode()`.

Nao usamos Azure Table Storage para a blacklist porque:
- A app corre com 1 worker (single process) — in-memory e suficiente
- Tokens expiram em horas — a blacklist auto-limpa-se
- Evita latencia adicional em cada request autenticado

**Alem disso**, vamos adicionar:
- Claim `jti` (JWT ID) para identificar tokens unicamente
- Claim `iat` (issued-at) para auditoria
- Account lockout temporario apos N tentativas falhadas de login
- Invalidacao global de tokens por user (force logout)

---

## Task 1 — Adicionar claims `jti` e `iat` ao JWT

**Ficheiro**: `auth.py`, funcao `jwt_encode` (linha 40)

### O que fazer

Adicionar `jti` (UUID4 hex) e `iat` (timestamp ISO UTC) automaticamente:

```python
import uuid

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
```

### Regras

1. `jti` e `iat` sao opcionais no encode — se ja vierem no payload, manter
2. `jti` usa `uuid.uuid4().hex` (32 chars hex, sem hifens)
3. `iat` usa `datetime.now(timezone.utc).isoformat()` (mesmo formato que `exp`)
4. Adicionar `import uuid` ao topo do ficheiro
5. **Compatibilidade**: tokens antigos (sem `jti`/`iat`) devem continuar a ser aceites no decode — NAO exigir estes claims na validacao

---

## Task 2 — Token blacklist in-memory

**Ficheiro**: `auth.py`

### O que fazer

Criar um modulo-level blacklist com cleanup automatico:

```python
import threading

# Token blacklist — in-memory, auto-cleanup de tokens expirados
_token_blacklist: dict[str, datetime] = {}  # {jti: exp_datetime}
_blacklist_lock = threading.Lock()

# User-level invalidation — todos os tokens emitidos antes deste timestamp sao invalidos
_user_invalidated_before: dict[str, datetime] = {}  # {username: invalidated_at}
_user_invalidated_lock = threading.Lock()


def blacklist_token(jti: str, exp: datetime) -> None:
    """Adiciona um token (por jti) a blacklist ate expirar."""
    if not jti:
        return
    with _blacklist_lock:
        _token_blacklist[jti] = exp


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
    with _user_invalidated_lock:
        cutoff = _user_invalidated_before.get(username)
    if cutoff is None:
        return False
    return iat <= cutoff


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
```

### Regras

1. Usar `threading.Lock` (nao `asyncio.Lock`) — o blacklist check esta no
   `jwt_decode()` que e sync. O GIL garante safety para dict reads, mas
   o lock protege writes concorrentes
2. `cleanup_blacklist()` sera chamado periodicamente (ver Task 5)
3. `_user_invalidated_before` permite force-logout de um user (admin action)
4. A blacklist e **best-effort** — se o processo reiniciar, a blacklist
   perde-se. Isto e aceitavel porque:
   - Um restart = novo processo = todas as sessions anteriores sao efectivamente
     terminadas (cookie cookie pode persistir, mas o risk window e limitado)
   - Alternativa (Table Storage) adicionaria latencia a cada request

---

## Task 3 — Integrar blacklist no jwt_decode

**Ficheiro**: `auth.py`, funcao `_jwt_decode_single` (linha 64)

### O que fazer

Apos validar a assinatura e expiracao, verificar blacklist:

```python
def _jwt_decode_single(token: str, secret: str) -> dict:
    """Decode JWT com um unico secret. Raises ValueError se falhar."""
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
        raise ValueError("Token exp invalido")
    exp = datetime.fromisoformat(exp_raw)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > exp:
        raise ValueError("Token expired")

    # --- NOVO: blacklist check ---
    jti = payload.get("jti")
    if jti and is_token_blacklisted(jti):
        raise ValueError("Token revoked")

    # --- NOVO: user-level invalidation check ---
    sub = payload.get("sub", "")
    iat_raw = payload.get("iat")
    if sub and iat_raw and isinstance(iat_raw, str):
        iat = datetime.fromisoformat(iat_raw)
        if iat.tzinfo is None:
            iat = iat.replace(tzinfo=timezone.utc)
        if is_user_token_invalidated(sub, iat):
            raise ValueError("Token invalidated by admin")

    return payload
```

### Regras

1. Blacklist check e **apos** verificacao de assinatura e expiracao —
   nao gastar CPU no blacklist se o token ja e invalido por outras razoes
2. Se o token nao tem `jti` (token antigo), skip blacklist check — manter
   compatibilidade com tokens emitidos antes desta mudanca
3. Se o token nao tem `iat`, skip user-level invalidation check
4. A mensagem de erro deve ser generica para nao revelar detalhes ao atacante,
   mas "Token revoked" e aceitavel internamente

---

## Task 4 — Blacklist no logout + password change

**Ficheiro**: `app.py`

### O que fazer

**Logout** (linha 2987): Extrair `jti` e `exp` do token e adicionar a blacklist:

```python
@app.post("/api/auth/logout")
@limiter.limit("20/minute", key_func=_user_or_ip_rate_key)
async def logout(request: Request):
    # Blacklist do token actual
    token = (request.cookies.get(AUTH_COOKIE_NAME) or "").strip()
    if token:
        try:
            payload = jwt_decode(token)
            jti = payload.get("jti")
            exp_raw = payload.get("exp")
            if jti and exp_raw:
                exp = datetime.fromisoformat(exp_raw)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                blacklist_token(jti, exp)
        except ValueError:
            pass  # Token ja invalido, nada a fazer

    response = JSONResponse(content={"status": "ok"})
    secure_cookie = AUTH_COOKIE_SECURE if _request_is_https(request) else False
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value="",
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
        max_age=0,
    )
    return response
```

**Change Password** (linha 3032): Apos mudar password, invalidar todos os
tokens do user:

```python
@app.post("/api/auth/change-password")
@limiter.limit("20/minute", key_func=_user_or_ip_rate_key)
async def change_password(request: Request, payload: ChangePasswordRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    username = user.get("sub")
    safe_username = odata_escape(username)
    users = await table_query("Users", f"PartitionKey eq 'user' and RowKey eq '{safe_username}'", top=1)
    if not users: raise HTTPException(404, "User não encontrado")
    if not verify_password(payload.current_password, users[0].get("PasswordHash","")): raise HTTPException(401, "Password actual incorrecta")
    await table_merge("Users", {"PartitionKey":"user","RowKey":username,"PasswordHash":hash_password(payload.new_password)})

    # Invalidar todos os tokens existentes deste user
    invalidate_user_tokens(username)

    return {"status":"ok"}
```

**Admin Reset Password** (linha 3044): Tambem invalidar tokens do user
cujo password foi resetado:

```python
@app.post("/api/auth/reset-password/{username}")
@limiter.limit("15/minute", key_func=_user_or_ip_rate_key)
async def admin_reset_password(request: Request, username: str, payload: LoginRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Apenas admins")
    await table_merge("Users", {"PartitionKey":"user","RowKey":username,"PasswordHash":hash_password(payload.password)})

    # Invalidar todos os tokens do user cujo password foi resetado
    invalidate_user_tokens(username)

    return {"status":"ok"}
```

**Deactivate User** (linha 3023): Invalidar tokens do user desactivado:

```python
@app.delete("/api/auth/users/{username}")
@limiter.limit("20/minute", key_func=_user_or_ip_rate_key)
async def deactivate_user(request: Request, username: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Apenas admins")
    if username == user.get("sub"): raise HTTPException(400, "Não podes desactivar-te")
    await table_merge("Users", {"PartitionKey":"user","RowKey":username,"IsActive":False})

    # Invalidar todos os tokens do user desactivado
    invalidate_user_tokens(username)

    return {"status":"ok"}
```

### Regras

1. Adicionar imports no topo de `app.py`:
   ```python
   from auth import blacklist_token, invalidate_user_tokens
   ```
2. No logout, se o token ja estiver invalido/expirado, ignorar silenciosamente
3. No change-password, invalidar DEPOIS de persistir o novo hash
4. No deactivate-user, invalidar tokens apos desactivar a conta
5. No admin-reset-password, invalidar tokens do user alvo (nao do admin)

---

## Task 5 — Account lockout + blacklist cleanup periodico

**Ficheiro**: `auth.py` (lockout) + `app.py` (cleanup)

### Account lockout

Adicionar tracking de login failures em `auth.py`:

```python
_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_DURATION_MINUTES = 15

_login_attempts: dict[str, list[float]] = {}  # {username: [timestamps]}
_login_attempts_lock = threading.Lock()


def record_login_failure(username: str) -> None:
    """Regista uma tentativa falhada de login."""
    if not username:
        return
    now = time.time()
    with _login_attempts_lock:
        attempts = _login_attempts.setdefault(username, [])
        attempts.append(now)
        # Manter apenas tentativas dentro da janela
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
        return len(recent) >= _MAX_LOGIN_ATTEMPTS


def clear_login_attempts(username: str) -> None:
    """Limpa tentativas falhadas apos login bem sucedido."""
    if not username:
        return
    with _login_attempts_lock:
        _login_attempts.pop(username, None)
```

Adicionar `import time` ao topo de `auth.py` se ainda nao existir.

### Integrar no login

**Ficheiro**: `app.py`, endpoint `/api/auth/login` (linha 2954)

```python
@app.post("/api/auth/login")
@limiter.limit("5/minute", key_func=_login_rate_key)
async def login(request: Request, login_request: LoginRequest):
    # Check account lockout ANTES de verificar credenciais
    if is_account_locked(login_request.username):
        raise HTTPException(
            429,
            f"Conta temporariamente bloqueada. Tenta novamente em {_LOCKOUT_DURATION_MINUTES} minutos."
        )

    safe_username = odata_escape(login_request.username)
    users = await table_query("Users", f"PartitionKey eq 'user' and RowKey eq '{safe_username}'", top=1)
    if not users:
        record_login_failure(login_request.username)
        raise HTTPException(401, "Credenciais inválidas")
    user = users[0]
    if not verify_password(login_request.password, user.get("PasswordHash","")):
        record_login_failure(login_request.username)
        raise HTTPException(401, "Credenciais inválidas")
    if user.get("IsActive") == False:
        raise HTTPException(403, "Conta desactivada")

    # Login sucedido — limpar tentativas falhadas
    clear_login_attempts(login_request.username)

    token = jwt_encode({"sub":login_request.username, "role":user.get("Role","user"), "name":user.get("DisplayName",login_request.username)})
    response = JSONResponse(
        content={
            "status": "ok",
            "username": login_request.username,
            "role": user.get("Role", "user"),
            "display_name": user.get("DisplayName", login_request.username),
        }
    )
    secure_cookie = AUTH_COOKIE_SECURE if _request_is_https(request) else False
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
    )
    return response
```

### Blacklist cleanup

Adicionar ao rate limiter cleanup existente em `app.py`. Procurar onde
`_rate_limiter_backend.cleanup_local_cache()` e chamado e adicionar ao
lado:

```python
from auth import cleanup_blacklist

# No middleware ou periodic cleanup:
cleanup_blacklist()
```

Se nao houver cleanup periodico explicito, adicionar ao middleware de rate
limiting, executando a cada ~5 minutos:

```python
_last_blacklist_cleanup = 0.0

# Dentro do middleware que processa requests:
nonlocal _last_blacklist_cleanup  # ou global
now = time.time()
if now - _last_blacklist_cleanup > 300:  # 5 minutos
    _last_blacklist_cleanup = now
    cleanup_blacklist()
```

### Regras

1. Adicionar imports em `app.py`:
   ```python
   from auth import (
       record_login_failure, is_account_locked, clear_login_attempts,
       cleanup_blacklist, blacklist_token, invalidate_user_tokens,
       _LOCKOUT_DURATION_MINUTES,
   )
   ```
2. Account lockout usa `threading.Lock` (sync) — chamado dentro de endpoints
   async mas o lock e rapido (in-memory dict operation)
3. Lockout check ANTES de verificar credenciais — evita timing attacks e
   reduz carga no Table Storage
4. `_MAX_LOGIN_ATTEMPTS = 5` e `_LOCKOUT_DURATION_MINUTES = 15` — hardcoded,
   sem necessidade de env vars para uso interno bancario
5. Nao retornar 401 quando conta bloqueada — retornar 429 (Too Many Requests)
   com mensagem generica

---

## Task 6 — Admin force-logout endpoint

**Ficheiro**: `app.py`

### O que fazer

Adicionar um endpoint admin para force-logout de um user:

```python
@app.post("/api/auth/force-logout/{username}")
@limiter.limit("10/minute", key_func=_user_or_ip_rate_key)
async def force_logout_user(request: Request, username: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin":
        raise HTTPException(403, "Apenas admins")
    invalidate_user_tokens(username)
    return {"status": "ok", "message": f"Tokens de {username} invalidados"}
```

### Regras

1. Colocar este endpoint junto dos outros endpoints admin de auth
   (apos deactivate-user ou reset-password)
2. Rate limit: 10/minute per user
3. Apenas admins podem executar
4. Usa `invalidate_user_tokens()` da Task 2

---

## Task 7 — Tests

**Ficheiro**: criar `tests/test_token_blacklist.py`

### Tests obrigatorios (minimo 10)

```python
import time
import pytest
from datetime import datetime, timedelta, timezone

class TestJWTClaims:
    """Verifica que jti e iat sao adicionados automaticamente."""

    def test_jwt_encode_adds_jti(self):
        """Token deve conter jti (32 chars hex)."""
        from auth import jwt_encode, jwt_decode
        token = jwt_encode({"sub": "test", "role": "user"})
        payload = jwt_decode(token)
        assert "jti" in payload
        assert len(payload["jti"]) == 32
        # Deve ser hex valido
        int(payload["jti"], 16)

    def test_jwt_encode_adds_iat(self):
        """Token deve conter iat (ISO timestamp)."""
        from auth import jwt_encode, jwt_decode
        token = jwt_encode({"sub": "test", "role": "user"})
        payload = jwt_decode(token)
        assert "iat" in payload
        iat = datetime.fromisoformat(payload["iat"])
        assert iat.tzinfo is not None

    def test_jwt_encode_preserves_custom_jti(self):
        """Se jti for fornecido, nao deve ser substituido."""
        from auth import jwt_encode, jwt_decode
        token = jwt_encode({"sub": "test", "jti": "custom123"})
        payload = jwt_decode(token)
        assert payload["jti"] == "custom123"

    def test_jwt_unique_jti_per_token(self):
        """Dois tokens devem ter jti diferentes."""
        from auth import jwt_encode, jwt_decode
        t1 = jwt_encode({"sub": "test"})
        t2 = jwt_encode({"sub": "test"})
        p1 = jwt_decode(t1)
        p2 = jwt_decode(t2)
        assert p1["jti"] != p2["jti"]

    def test_old_token_without_jti_still_valid(self):
        """Tokens antigos sem jti devem continuar a funcionar."""
        from auth import jwt_encode, jwt_decode, _b64url_encode
        import json, hmac, hashlib
        from config import JWT_SECRET
        # Criar token manualmente sem jti/iat
        exp = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        payload_data = {"sub": "old_user", "role": "user", "exp": exp}
        header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        pay = _b64url_encode(json.dumps(payload_data).encode())
        sig = _b64url_encode(hmac.new(JWT_SECRET.encode(), f"{header}.{pay}".encode(), hashlib.sha256).digest())
        old_token = f"{header}.{pay}.{sig}"
        result = jwt_decode(old_token)
        assert result["sub"] == "old_user"


class TestTokenBlacklist:
    """Verifica blacklist de tokens."""

    def test_blacklist_token_then_reject(self):
        """Token blacklisted deve ser rejeitado."""
        from auth import jwt_encode, jwt_decode, blacklist_token
        token = jwt_encode({"sub": "test"})
        payload = jwt_decode(token)
        jti = payload["jti"]
        exp = datetime.fromisoformat(payload["exp"])
        blacklist_token(jti, exp)
        with pytest.raises(ValueError, match="revoked"):
            jwt_decode(token)

    def test_non_blacklisted_token_accepted(self):
        """Token nao blacklisted deve ser aceite."""
        from auth import jwt_encode, jwt_decode, blacklist_token
        t1 = jwt_encode({"sub": "test"})
        t2 = jwt_encode({"sub": "test"})
        p1 = jwt_decode(t1)
        # Blacklist apenas t1
        blacklist_token(p1["jti"], datetime.fromisoformat(p1["exp"]))
        # t2 deve funcionar
        jwt_decode(t2)

    def test_cleanup_removes_expired(self):
        """cleanup_blacklist deve remover tokens expirados."""
        from auth import blacklist_token, is_token_blacklisted, cleanup_blacklist
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        blacklist_token("expired-jti", past)
        assert is_token_blacklisted("expired-jti")
        removed = cleanup_blacklist()
        assert removed >= 1
        assert not is_token_blacklisted("expired-jti")


class TestUserInvalidation:
    """Verifica invalidacao global de tokens por user."""

    def test_invalidate_user_tokens(self):
        """Tokens emitidos antes de invalidacao devem ser rejeitados."""
        from auth import jwt_encode, jwt_decode, invalidate_user_tokens
        token = jwt_encode({"sub": "victim"})
        # Pequeno delay para garantir que iat < invalidation time
        time.sleep(0.01)
        invalidate_user_tokens("victim")
        with pytest.raises(ValueError, match="invalidated"):
            jwt_decode(token)

    def test_new_token_after_invalidation_works(self):
        """Tokens emitidos DEPOIS de invalidacao devem funcionar."""
        from auth import jwt_encode, jwt_decode, invalidate_user_tokens
        invalidate_user_tokens("user_x")
        time.sleep(0.01)
        token = jwt_encode({"sub": "user_x"})
        payload = jwt_decode(token)
        assert payload["sub"] == "user_x"


class TestAccountLockout:
    """Verifica lockout de conta apos tentativas falhadas."""

    def test_not_locked_initially(self):
        from auth import is_account_locked
        assert not is_account_locked("fresh_user_locktest")

    def test_locked_after_max_attempts(self):
        from auth import record_login_failure, is_account_locked, _MAX_LOGIN_ATTEMPTS
        username = "lockout_test_user"
        for _ in range(_MAX_LOGIN_ATTEMPTS):
            record_login_failure(username)
        assert is_account_locked(username)

    def test_clear_attempts_unlocks(self):
        from auth import record_login_failure, is_account_locked, clear_login_attempts, _MAX_LOGIN_ATTEMPTS
        username = "clear_test_user"
        for _ in range(_MAX_LOGIN_ATTEMPTS):
            record_login_failure(username)
        assert is_account_locked(username)
        clear_login_attempts(username)
        assert not is_account_locked(username)
```

### Regras para testes

1. Cada test deve ser **independente** — usar usernames unicos para evitar
   interferencia entre tests (a blacklist e module-level)
2. Nao mockar a blacklist — testar o mecanismo real
3. Importar de `auth` directamente
4. Usar `time.sleep(0.01)` apenas onde necessario para garantir ordering
   temporal (iat vs invalidation time)
5. Os testes existentes (214+) devem continuar a passar sem modificacao

---

## Restricoes Globais

1. **NAO alterar a interface publica** dos endpoints FastAPI (rotas, request/response schemas)
   EXCEPTO o novo endpoint `/api/auth/force-logout/{username}` (Task 6)
2. **NAO modificar testes existentes** — os 214+ testes actuais devem continuar a passar
3. **NAO adicionar dependencias** — usar apenas stdlib (`uuid`, `threading`, `time`)
4. **NAO usar Azure Table Storage** para a blacklist — manter in-memory
5. **Manter logging existente** — nao alterar niveis de log
6. **Compatibilidade com tokens antigos** — tokens sem `jti`/`iat` devem
   continuar a funcionar (skip blacklist/invalidation checks)
7. Branch deve ser criada a partir de `main` (commit actual)
8. Correr `python -m pytest tests/ -x --tb=short -q` antes de submeter

---

## Ficheiros a modificar

| Ficheiro | Alteracao |
|---|---|
| `auth.py` | Claims jti/iat, blacklist, user invalidation, account lockout |
| `app.py` | Integrar blacklist no logout/change-password/deactivate/reset, account lockout no login, force-logout endpoint, cleanup periodico |
| `tests/test_token_blacklist.py` | NOVO — minimo 10 tests |

---

## Commit History esperado

```
commit 1: feat: add jti and iat claims to JWT tokens
commit 2: feat: add in-memory token blacklist with cleanup
commit 3: feat: integrate blacklist in logout and password changes
commit 4: feat: add account lockout after failed login attempts
commit 5: feat: add admin force-logout endpoint
commit 6: test: add token blacklist and auth hardening tests (10+ tests)
```
