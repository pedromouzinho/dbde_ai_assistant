# Deploy v7.2.0 — Instruções para o Codex
## Emitido por: Claude (Arquiteto) | Data: 2026-02-23

---

## CONTEXTO

A v7.2.0 está validada localmente mas a produção ainda corre v7.1.1. Este deploy inclui:
- Fase 4.7 completa (security hardening: WIQL sanitization, CORS whitelist, XSS DOMPurify, rate limiting, lock fixes)
- Fase 5 completa (tool registry, Figma/Miro read-only, streaming perf, httpOnly cookies, feedback cap)

É o maior deploy desde a v7.0. Requer cuidado.

---

## SEQUÊNCIA DE DEPLOY

### Passo 1 — Configurar App Settings (tokens + novas env vars)

Antes do deploy do código, configurar as seguintes App Settings no Azure App Service. Usar o método que já usas (az CLI ou Kudu API).

**Novas env vars a adicionar:**

```
FIGMA_ACCESS_TOKEN = figd_4PQ2K9RLheWD3Z6I3_eBulCOW6gh-ZAuuNJGiabm
MIRO_ACCESS_TOKEN = eyJtaXJvLm9yaWdpbiI6ImV1MDEifQ_eaJKa6zqOQx7Gph68TDlErWB2tQ
```

**Env vars existentes a verificar (não alterar se já correctas):**
- `ALLOWED_ORIGINS` — deve existir da 4.7.2. Se não existir, a whitelist default no config.py é usada.
- `ADMIN_INITIAL_PASSWORD` — deve existir da 4.7.5.

**IMPORTANTE:** Os tokens acima são segredos. Configurar apenas nas App Settings. Nunca em código, logs, ou outputs.

---

### Passo 2 — Preparar ZIP de deploy

Construir o ZIP com TODOS os ficheiros da aplicação, incluindo:

**Ficheiros novos (Fase 5):**
- `tool_registry.py`
- `tools_figma.py`
- `tools_miro.py`

**Ficheiros alterados (Fases 4.7 + 5):**
- `app.py` (rate limiting, httpOnly cookies, CORS middleware, feedback deque)
- `auth.py` (cookie token resolution)
- `agent.py` (registry imports)
- `tools.py` (registry migration, WIQL sanitization, prompt awareness)
- `config.py` (v7.2.0, ALLOWED_ORIGINS, tokens env vars, AUTH_COOKIE_NAME)
- `export_engine.py` (html.escape hardening)
- `learning.py` (cache refresh lock)
- `storage.py` (admin password env var)
- `static/index.html` (DOMPurify, streaming perf, httpOnly, v7.2.0 labels)
- `requirements.txt` (slowapi)

**Ficheiros inalterados mas incluir no ZIP:**
- `models.py`
- `llm_provider.py`
- `startup.sh`
- `static/` (outros assets se existirem)
- `antenv/` (dependências — incluir como está)

**NÃO incluir no ZIP:**
- `*.md` (docs de auditoria, handoff, instruções — não são código)
- `__pycache__/`
- `.git/`

---

### Passo 3 — ZIP Deploy

Usar zipdeploy via Kudu (método que já funcionou para v7.1.1):

```
POST https://<app-name>.scm.azurewebsites.net/api/zipdeploy
```

Com o ZIP no body e credenciais de deploy.

Aguardar o deploy completar (pode demorar 2-5 min com Oryx build).

---

### Passo 4 — Validação Pós-Deploy (OBRIGATÓRIO)

Executar TODAS as verificações abaixo. Se qualquer uma falhar, reportar imediatamente — NÃO tentar corrigir sem instruções.

#### 4.1 Health Check
```
GET /health → 200
```

#### 4.2 Version Check
```
GET /api/info → body deve conter "7.2.0"
```

#### 4.3 Login (httpOnly cookie)
```
POST /api/auth/login → 200
- Response deve ter Set-Cookie header com "dbde_token=..."
- Cookie deve ter flags: HttpOnly, Secure, SameSite=Lax
- Response body NÃO deve conter "access_token"
```

#### 4.4 Auth via Cookie
```
GET /api/auth/me (com cookie do passo anterior) → 200 com dados do user
```

#### 4.5 Chat (funcionalidade core)
```
POST /chat/agent (com cookie) → 200
- Testar com pergunta simples: "Quantos work items existem no estado Active?"
- Deve retornar resposta com tool_calls executados
```

#### 4.6 Rate Limiting
```
POST /api/auth/login × 6 em sequência rápida → 6º deve retornar 429
- Response body: {"detail": "Limite de pedidos excedido..."}
- Header: Retry-After presente
```

#### 4.7 CORS Blocking
```
Request com Origin header não-listado → 403
- curl -H "Origin: https://evil.com" <api-endpoint> → 403
```

#### 4.8 Figma Integration (se token configurado)
```
GET /api/info ou verificar via chat:
- "Lista os ficheiros recentes do Figma" → deve retornar dados (ou erro de auth se token inválido)
- Se retornar 401/403 da API Figma, reportar — pode ser token expirado
```

#### 4.9 Miro Integration (se token configurado)
```
- "Lista os boards do Miro" → deve retornar dados (ou erro de auth se token inválido)
- Se retornar 401/403 da API Miro, reportar
```

#### 4.10 Streaming
```
POST /chat/agent/stream → SSE stream funcional
- Verificar que tokens chegam incrementalmente
- Resposta final deve estar completa e formatada
```

---

### Passo 5 — Reportar Resultado

Reportar com o seguinte formato:

```
DEPLOY v7.2.0 — RESULTADO
==========================
Health:     OK / FAIL
Version:    7.2.0 / <outro>
Login:      OK / FAIL (detalhe)
Cookie:     HttpOnly OK / FAIL (detalhe)
Auth/me:    OK / FAIL
Chat:       OK / FAIL
Rate Limit: OK / FAIL
CORS Block: OK / FAIL
Figma:      OK / FAIL / SKIP (sem token)
Miro:       OK / FAIL / SKIP (sem token)
Streaming:  OK / FAIL
==========================
VEREDICTO: DEPLOY SUCCESS / DEPLOY FAILED (detalhe)
```

---

## CRITÉRIO DE ABORT

- Se `/health` falhar após 5 minutos → verificar logs do Oryx build
- Se login falhar → pode ser o cookie path ou a falta de middleware. Verificar logs
- Se slowapi causar import error → verificar que requirements.txt tem `slowapi==0.1.9` e que o Oryx build instalou
- Se Figma/Miro retornar auth error → reportar, não é blocker para o deploy (graceful disable)
- Se a app entrar em restart loop → reverter imediatamente para o ZIP anterior (v7.1.1)

**NUNCA** fazer deploy parcial. É tudo ou nada.

---

## ROLLBACK

Se o deploy falhar e não for recuperável em 15 minutos:
1. Fazer ZIP deploy do backup v7.1.1
2. Remover as App Settings novas que possam causar conflito (FIGMA_ACCESS_TOKEN, MIRO_ACCESS_TOKEN) — não são usadas pela v7.1.1
3. Reportar o que falhou para análise

---

— Claude (Arquiteto), 2026-02-23
