# Codex Task: PII Shield Phase 2 — Tool Output Masking & Hardening

## Context

Phase 1 (commit `2f5ae68`, branch `codex/pii-shield-phase1-hardening`) added:
- Regex pre-filter (`_regex_pre_mask`) for NIF, IBAN, credit card, email, phone, SWIFT
- Per-category confidence thresholds (`_CONFIDENCE_THRESHOLDS`)
- Overlapping entity resolution (`_resolve_overlapping_entities`)
- Placeholder protection against Azure re-detection (`_span_overlaps_placeholders`)

Phase 2 closes remaining gaps: tool results that re-enter the LLM context unmasked, web search query leakage, HTTP client waste, and audit logging.

## Architecture Overview

The agent loop works as follows:
1. User message enters `agent_chat()` (`agent.py:1397`) or `agent_chat_stream()` (`agent.py:1564`)
2. Messages are built via `_build_llm_messages()` and sent to `llm_with_fallback()` (`llm_provider.py:808`)
3. Inside `llm_with_fallback()`, `mask_messages()` is called (`llm_provider.py:825`) — but `mask_messages()` only masks `role="user"` messages (`pii_shield.py:330`)
4. LLM may return tool calls → executed by `_execute_tool_calls()` (`agent.py:1038`)
5. Tool results are added to conversation as `role="tool"` messages (`agent.py:1383-1388`) — **these are NOT masked**
6. Tool results are also stored in blob storage (`agent.py:1359-1361`) — **unmasked PII in blob**
7. The agent loop calls `llm_with_fallback()` again (`agent.py:1517-1522`) with the full conversation including unmasked tool results
8. `mask_messages()` skips `role="tool"` → PII in tool results is sent to the LLM in cleartext

The same flow applies to streaming via `llm_stream_with_fallback()` (`llm_provider.py:908`).

## Task 1: Extend `mask_messages()` to mask tool results

**File:** `pii_shield.py`
**Function:** `mask_messages()` (line 326)

Currently the function only processes `role="user"` messages:
```python
if msg.get("role") != "user":
    masked_messages.append(msg)
    continue
```

### Requirements:
1. Change the condition to also process `role="tool"` messages
2. Tool messages have `content` as a string (JSON-serialized tool result). Mask the string content the same way as user text content
3. Do NOT mask `role="system"` or `role="assistant"` messages
4. Preserve all other fields on the message dict (`tool_call_id`, `result_blob_ref`, etc.)

### Implementation:
```python
async def mask_messages(messages: List[dict], context: PIIMaskingContext) -> List[dict]:
    """Mascara PII em mensagens do utilizador e resultados de tools."""
    masked_messages: List[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        if role not in ("user", "tool"):
            masked_messages.append(msg)
            continue

        content = msg.get("content", "")
        if isinstance(content, str):
            masked_content = await mask_pii(content, context)
            masked_messages.append({**msg, "content": masked_content})
            continue

        if isinstance(content, list):
            masked_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    masked_text = await mask_pii(str(part.get("text", "")), context)
                    masked_parts.append({**part, "text": masked_text})
                else:
                    masked_parts.append(part)
            masked_messages.append({**msg, "content": masked_parts})
            continue

        masked_messages.append(msg)

    return masked_messages
```

## Task 2: Mask PII in tool results before blob storage

**File:** `agent.py`
**Function:** `_execute_tool_calls()` (line 1038)

Tool results are persisted to blob storage at line 1359-1361 without PII masking. The blob container `chat-tool-results` may contain cleartext PII from DevOps work items, uploaded documents, web search results, etc.

### Requirements:
1. Import `mask_pii` and `PIIMaskingContext` from `pii_shield` in `agent.py`
2. Import `PII_ENABLED` from `config`
3. Before persisting to blob storage, if `PII_ENABLED`, mask the serialized tool result
4. Use a dedicated `PIIMaskingContext` per blob (we do NOT need to unmask blob storage — it's for audit/compliance)
5. The masking is fire-and-forget for blob only — the conversation copy remains as-is (it will be masked later by `mask_messages` in Task 1)
6. **CRITICAL fallback**: if `json.loads(masked_serialized)` fails (e.g. placeholders inside numeric JSON values break parsing), store `{"masked_content": masked_serialized}` as the blob payload — **never** fall back to the original unmasked `tool_result`

### Implementation location: `agent.py` around line 1353-1361

Replace the blob upload block:
```python
result_blob_ref = ""
try:
    safe_user = _safe_blob_component(user_sub or "anon", 80)
    safe_conv = _safe_blob_component(conv_id, 80)
    safe_tool = _safe_blob_component(tc.name, 40)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    blob_name = f"{safe_user}/{safe_conv}/{ts}_{safe_tool}_{_safe_blob_component(tc.id, 60)}.json"

    blob_payload = tool_result
    if PII_ENABLED:
        try:
            blob_ctx = PIIMaskingContext()
            serialized = json.dumps(tool_result, ensure_ascii=False, default=str)
            masked_serialized = await mask_pii(serialized, blob_ctx)
            try:
                blob_payload = json.loads(masked_serialized)
            except (json.JSONDecodeError, ValueError):
                # Placeholders in numeric values may break JSON structure.
                # Store the masked string as-is — NEVER fall back to unmasked PII.
                blob_payload = {"masked_content": masked_serialized}
        except Exception as mask_err:
            logger.warning("[Agent] PII masking for blob failed (%s): %s", tc.name, mask_err)
            # Even on masking failure, do NOT store cleartext PII.
            # Store a redacted placeholder instead.
            blob_payload = {"error": "pii_masking_failed", "tool": tc.name}

    uploaded = await blob_upload_json(CHAT_TOOLRESULT_BLOB_CONTAINER, blob_name, blob_payload)
    result_blob_ref = str(uploaded.get("blob_ref", "") or "")
except Exception as e:
    logger.warning("[Agent] tool result blob persist failed (%s): %s", tc.name, e)
```

## Task 3: Mask web search queries before sending to Brave API

**File:** `tools_knowledge.py`
**Function:** `tool_search_web()` (line 233)

The user's question is passed as `query` to the Brave Search API (line 275). If the user asks "pesquisa o NIF 123456789", the NIF is sent to an external API.

### Requirements:
1. Import `_regex_pre_mask` and `PIIMaskingContext` from `pii_shield`
2. Before sending the query to Brave, apply `_regex_pre_mask()` to strip PII from the query
3. If masking removes content, log a warning
4. Store original query for the result dict, use masked query for the API call
5. Use the synchronous `_regex_pre_mask` only (no async Azure call needed — we don't want to add latency to web search)

### Implementation: in `tool_search_web()` after line 238 (`query = str(query or "").strip()[:200]`)

```python
# PII safety: strip sensitive patterns from query before sending to external API
original_query = query
_pii_ctx = PIIMaskingContext()
query = _regex_pre_mask(query, _pii_ctx)
if _pii_ctx.mappings:
    logging.warning(
        "[WebSearch] PII stripped from query before Brave API: %d patterns masked",
        len(_pii_ctx.mappings),
    )
```

Then use `original_query` in the result dict (line 301) so the user sees their original query, but `query` (masked) goes to Brave.

Change line 301:
```python
result = {
    "query": original_query,  # show original to user
    ...
}
```

## Task 4: Share `httpx.AsyncClient` across PII Shield calls

**File:** `pii_shield.py`
**Function:** `mask_pii()` (line 238)

Currently creates a new `httpx.AsyncClient` per call (line 270). In multi-tool agent iterations, this wastes TCP connections and adds latency.

### Requirements:
1. Create a module-level `httpx.AsyncClient` instance with connection pooling
2. Use it in `mask_pii()` instead of creating a new client each time
3. Add a cleanup function for graceful shutdown
4. Keep the timeout at 10 seconds per request

### Implementation:

Add at module level (after line 81):
```python
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


async def close_http_client():
    """Call on app shutdown to cleanly close the shared HTTP client."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None
```

Then in `mask_pii()`, replace line 270:
```python
# OLD: async with httpx.AsyncClient(timeout=10.0) as client:
client = _get_http_client()
resp = await client.post(
    url,
    json=payload,
    headers={
        "Ocp-Apim-Subscription-Key": PII_API_KEY,
        "Content-Type": "application/json",
    },
)
resp.raise_for_status()
```

Remove the `async with` context manager — the shared client is not closed per-request.

Also register `close_http_client()` in the app shutdown. The app uses `lifespan` context manager in `app.py` (line 294-300), **not** the deprecated `@app.on_event`. Add the call inside `shutdown_event()` (`app.py:1095`), alongside the existing `http_client.aclose()`, `_close_knowledge_client()`, etc.:

```python
# app.py — inside shutdown_event() (line 1095), add after the existing close calls:
from pii_shield import close_http_client

async def shutdown_event():
    # ... existing cleanup ...
    await close_all_providers()
    await close_http_client()  # <-- ADD THIS LINE
```

**Do NOT use `@app.on_event("shutdown")`** — it is deprecated and would break the existing `test_lifespan` tests.

## Task 5: PII audit logging

**File:** `pii_shield.py`
**Function:** `mask_pii()` (line 238)

### Requirements:
1. After masking (both regex and Azure), log a structured JSON audit entry with:
   - Number of entities masked by regex pre-filter
   - Number of entities masked by Azure API
   - Categories detected (list of unique category names)
   - Whether Azure API was used or only regex fallback
   - No PII values in the log (only counts and categories)
2. Use `logger.info()` with a JSON-serializable dict
3. Only log when at least one entity was masked

### Implementation:

At the end of `_regex_pre_mask()`, before returning, track the count:
```python
# Return value already set as `masked`
# The caller can check len(context.mappings) to know how many regex matches occurred
```

In `mask_pii()`, after the Azure processing block and before the final return, add:
```python
regex_count = len(context.mappings) - masked_count  # regex happened before Azure
audit = {
    "event": "pii_shield_audit",
    "regex_masked": regex_count,
    "azure_masked": masked_count,
    "categories": list(set(
        entity.get("category", "UNKNOWN") for entity in entities
    )),
    "azure_used": True,
    "text_length": len(text),
}
logger.info("[PIIShieldAudit] %s", json.dumps(audit, ensure_ascii=False))
```

For the regex-only fallback path (when Azure is not configured or fails), add similar logging:
```python
if PII_ENABLED and (not PII_ENDPOINT or not PII_API_KEY):
    result = _regex_pre_mask(text, context)
    if context.mappings:
        audit = {
            "event": "pii_shield_audit",
            "regex_masked": len(context.mappings),
            "azure_masked": 0,
            "categories": list(set(
                # extract category from placeholder like [NIF_1] -> NIF
                ph.strip("[]").rsplit("_", 1)[0]
                for ph in context.mappings.keys()
            )),
            "azure_used": False,
            "text_length": len(text),
        }
        logger.info("[PIIShieldAudit] %s", json.dumps(audit, ensure_ascii=False))
    return result
```

## Task 6: Tests

**File:** `tests/test_pii_shield_hardening.py`

Add a new test class `TestPhase2` with the following tests:

### 6.1 Test tool message masking
```python
class TestPhase2:
    """Tests for PII Shield Phase 2 hardening."""

    @pytest.mark.asyncio
    async def test_mask_messages_masks_tool_role(self, monkeypatch):
        """mask_messages should mask role='tool' content."""
        monkeypatch.setattr(pii_shield, "PII_ENABLED", True)
        monkeypatch.setattr(pii_shield, "PII_ENDPOINT", "")
        monkeypatch.setattr(pii_shield, "PII_API_KEY", "")

        ctx = PIIMaskingContext()
        messages = [
            {"role": "user", "content": "Procura o NIF 123456789"},
            {"role": "assistant", "content": "Vou procurar."},
            {"role": "tool", "tool_call_id": "tc1", "content": '{"result": "NIF encontrado: 123456789"}'},
        ]
        from pii_shield import mask_messages
        result = await mask_messages(messages, ctx)

        # User message should be masked
        assert "123456789" not in result[0]["content"]
        # Assistant message should NOT be masked
        assert result[1]["content"] == "Vou procurar."
        # Tool message should be masked
        assert "123456789" not in result[2]["content"]
        # tool_call_id should be preserved
        assert result[2]["tool_call_id"] == "tc1"
        # Unmask should restore originals
        assert "123456789" in ctx.unmask(result[2]["content"])
```

### 6.2 Test system messages are not masked
```python
    @pytest.mark.asyncio
    async def test_mask_messages_skips_system_and_assistant(self, monkeypatch):
        monkeypatch.setattr(pii_shield, "PII_ENABLED", True)
        monkeypatch.setattr(pii_shield, "PII_ENDPOINT", "")
        monkeypatch.setattr(pii_shield, "PII_API_KEY", "")

        ctx = PIIMaskingContext()
        messages = [
            {"role": "system", "content": "NIF do admin: 123456789"},
            {"role": "assistant", "content": "O NIF é 123456789"},
        ]
        from pii_shield import mask_messages
        result = await mask_messages(messages, ctx)

        # Neither should be masked
        assert result[0]["content"] == "NIF do admin: 123456789"
        assert result[1]["content"] == "O NIF é 123456789"
        assert len(ctx.mappings) == 0
```

### 6.3 Test web search query masking
```python
    def test_regex_pre_mask_strips_nif_from_query(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("pesquisa o NIF 123456789 no google", ctx)
        assert "123456789" not in result
        assert len(ctx.mappings) == 1

    def test_regex_pre_mask_strips_iban_from_query(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("procura IBAN PT50000201231234567890154", ctx)
        assert "PT50" not in result
        assert len(ctx.mappings) == 1
```

### 6.4 Test shared HTTP client
```python
    def test_shared_http_client_creation(self):
        from pii_shield import _get_http_client
        client = _get_http_client()
        assert client is not None
        assert not client.is_closed
        # Same instance on second call
        client2 = _get_http_client()
        assert client is client2
```

### 6.5 Test audit logging
```python
    @pytest.mark.asyncio
    async def test_audit_log_emitted_on_regex_masking(self, monkeypatch, caplog):
        monkeypatch.setattr(pii_shield, "PII_ENABLED", True)
        monkeypatch.setattr(pii_shield, "PII_ENDPOINT", "")
        monkeypatch.setattr(pii_shield, "PII_API_KEY", "")

        import logging
        with caplog.at_level(logging.INFO, logger="pii_shield"):
            ctx = PIIMaskingContext()
            await mask_pii("NIF: 123456789", ctx)

        assert any("pii_shield_audit" in record.message for record in caplog.records)
```

## Execution Order

1. Task 4 first (shared HTTP client) — no dependencies, isolated change
2. Task 1 (extend mask_messages) — core vulnerability fix
3. Task 5 (audit logging) — builds on mask_pii
4. Task 2 (blob storage masking) — depends on Task 1 imports
5. Task 3 (web search query masking) — independent of others
6. Task 6 (tests) — after all implementation tasks

## Files Modified

| File | Changes |
|------|---------|
| `pii_shield.py` | Shared HTTP client, extend `mask_messages()`, audit logging |
| `agent.py` | Import PII utilities, mask tool results before blob storage |
| `tools_knowledge.py` | Import `_regex_pre_mask`, mask web search queries |
| `app.py` | Add `close_http_client()` call inside `shutdown_event()` (lifespan pattern, line 1095) |
| `tests/test_pii_shield_hardening.py` | Add `TestPhase2` class with 6+ tests |

## Branch

Create branch `codex/pii-shield-phase2-tool-output-masking` from `main`.

Expected history on the new branch after Phase 2 commit:
```
036d587  (main)        — base
8af9aa9  Phase 1       — cherry-pick of PII Shield hardening
fb008fe  Phase 2       — this task's commit
```

Phase 1 code (commit `2f5ae68`, cherry-picked as `8af9aa9`) is already on `main`.

## Validation

Run all tests:
```bash
python -m pytest tests/test_pii_shield_hardening.py -v
```

Ensure:
- All Phase 1 tests (22) still pass
- All Phase 2 tests pass
- No import errors
- No circular imports (pii_shield ← agent.py is a new dependency direction — verify it doesn't create cycles)

## Important Notes

- **Do NOT mask `role="assistant"` messages** — the LLM's own output should not be re-masked
- **Do NOT mask `role="system"` messages** — system prompts are controlled by us, not user data
- The `_regex_pre_mask()` function is synchronous — safe to call from sync contexts like `tool_search_web()`
- The audit log must NEVER contain PII values — only counts and category names
- The shared HTTP client must handle the case where the event loop is closed (use `is_closed` check)
- Blob storage masking is a defense-in-depth measure — even if conversation masking works, blobs should not contain cleartext PII
