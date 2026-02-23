# =============================================================================
# agent.py — Agent loop com streaming SSE v7.0
# =============================================================================
# Suporta 2 modos de execução:
# 1. agent_chat() — request/response clássico (retorna AgentChatResponse)
# 2. agent_chat_stream() — SSE streaming (yield StreamEvents)
# =============================================================================

import json
import uuid
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, AsyncGenerator, Callable, Iterator
from collections.abc import MutableMapping

from config import (
    AGENT_MAX_ITERATIONS, AGENT_MAX_TOKENS, AGENT_TEMPERATURE,
    AGENT_HISTORY_LIMIT, LLM_DEFAULT_TIER,
)
from models import (
    AgentChatRequest, AgentChatResponse, LLMToolCall, StreamEvent,
)
from llm_provider import (
    get_provider, llm_with_fallback,
    make_tool_result_message, make_assistant_message_from_response,
)
from learning import get_learned_rules, get_few_shot_examples
from tools import (
    TOOLS, execute_tool, truncate_tool_result,
    get_agent_system_prompt, get_userstory_system_prompt,
)
from storage import table_insert, table_merge, table_query

# =============================================================================
# IN-MEMORY STORES (migra para persistent storage em fase futura)
# =============================================================================
MAX_CONVERSATIONS = 200
CONVERSATION_TTL_SECONDS = 4 * 3600


class ConversationStore(MutableMapping[str, List[dict]]):
    """Store em memória com TTL + eviction LRU."""

    def __init__(
        self,
        max_conversations: int,
        ttl_seconds: int,
        on_evict: Optional[Callable[[str], None]] = None,
    ):
        self._data: Dict[str, List[dict]] = {}
        self._last_accessed: Dict[str, datetime] = {}
        self.max_conversations = max_conversations
        self.ttl_seconds = ttl_seconds
        self._on_evict = on_evict

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.utcnow()

    def _touch(self, key: str) -> None:
        self._last_accessed[key] = self._utcnow()

    def _evict(self, key: str, reason: str) -> None:
        self._data.pop(key, None)
        self._last_accessed.pop(key, None)
        if self._on_evict:
            self._on_evict(key)
        print(f"[ConversationStore] Evicted {key} ({reason})")

    def _is_expired(self, key: str, now: datetime) -> bool:
        last = self._last_accessed.get(key)
        if not last:
            return False
        return (now - last).total_seconds() > self.ttl_seconds

    def cleanup_expired(self) -> List[str]:
        now = self._utcnow()
        expired = [k for k in list(self._data.keys()) if self._is_expired(k, now)]
        for key in expired:
            self._evict(key, reason="ttl")
        return expired

    def _evict_lru(self, exclude_key: Optional[str] = None) -> Optional[str]:
        candidates = [
            (ts, key)
            for key, ts in self._last_accessed.items()
            if key in self._data and key != exclude_key
        ]
        if not candidates:
            return None
        _, oldest_key = min(candidates, key=lambda item: item[0])
        self._evict(oldest_key, reason="lru")
        return oldest_key

    def ensure_capacity_for_new(self, new_key: str) -> None:
        self.cleanup_expired()
        while len(self._data) >= self.max_conversations:
            if self._evict_lru(exclude_key=new_key) is None:
                break

    def touch(self, key: str) -> None:
        if key in self._data:
            self._touch(key)

    def __getitem__(self, key: str) -> List[dict]:
        value = self._data[key]
        self._touch(key)
        return value

    def __setitem__(self, key: str, value: List[dict]) -> None:
        if key not in self._data:
            self.ensure_capacity_for_new(new_key=key)
        self._data[key] = value
        self._touch(key)

    def __delitem__(self, key: str) -> None:
        if key not in self._data:
            raise KeyError(key)
        self._evict(key, reason="manual")

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: str, default=None):
        if key in self._data:
            self._touch(key)
            return self._data[key]
        return default

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()


conversation_meta: Dict[str, Dict] = {}
uploaded_files_store: Dict[str, Dict] = {}


def _cleanup_conversation_related_state(conv_id: str) -> None:
    conversation_meta.pop(conv_id, None)
    uploaded_files_store.pop(conv_id, None)


conversations = ConversationStore(
    max_conversations=MAX_CONVERSATIONS,
    ttl_seconds=CONVERSATION_TTL_SECONDS,
    on_evict=_cleanup_conversation_related_state,
)
logger = logging.getLogger(__name__)

# =============================================================================
# HISTORY MANAGEMENT
# =============================================================================

def _trim_history(messages: List[dict]) -> List[dict]:
    """Gere histórico: mantém system msgs + últimas N non-system msgs."""
    if len(messages) <= 20:
        return messages
    
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    other = [m for m in messages if m.get("role") != "system"]
    kept = other[-AGENT_HISTORY_LIMIT:]
    
    # Comprimir tool results antigos
    for i, msg in enumerate(kept):
        if msg.get("role") == "tool" and len(msg.get("content", "")) > 500:
            try:
                data = json.loads(msg["content"])
                summary = {"total_count": data.get("total_count", "?"), "items_returned": len(data.get("items", [])), "_compressed": True}
                kept[i] = {**msg, "content": json.dumps(summary, ensure_ascii=False)}
            except (json.JSONDecodeError, TypeError):
                kept[i] = {**msg, "content": msg["content"][:200] + "...(truncado)"}
    
    return sys_msgs + kept


def _build_user_message(request: AgentChatRequest) -> dict:
    """Constrói a mensagem do user (texto ou texto+imagem)."""
    if request.image_base64:
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": request.question},
                {"type": "image_url", "image_url": {
                    "url": f"data:{request.image_content_type};base64,{request.image_base64}"
                }},
            ],
        }
    return {"role": "user", "content": request.question}


def _inject_file_context(conv_id: str, messages: List[dict]):
    """Injeta contexto de ficheiro uploaded na conversa."""
    file_data = uploaded_files_store.get(conv_id)
    if file_data and not conversation_meta.get(conv_id, {}).get("file_injected"):
        ctx = (
            f"FICHEIRO CARREGADO:\nFicheiro: {file_data['filename']}\n"
            f"Linhas: {file_data['row_count']}\nColunas: {', '.join(file_data['col_names'])}\n"
            f"{'NOTA: Dados truncados.' if file_data.get('truncated') else ''}\n\n"
            f"CONTEÚDO:\n{file_data['data_text'][:50000]}"
        )
        messages.append({"role": "system", "content": ctx})
        conversation_meta.setdefault(conv_id, {})["file_injected"] = True


async def _load_conversation_from_storage(conv_id: str) -> bool:
    """Tenta carregar conversa do Table Storage. Retorna True se encontrou."""
    try:
        rows = await table_query(
            "ChatHistory",
            f"PartitionKey eq 'chat' and RowKey eq '{conv_id}'",
            top=1,
        )
        if not rows:
            return False

        row = rows[0]
        messages_json = row.get("Messages", "[]")
        messages = json.loads(messages_json)

        if not messages:
            return False

        # Verificar que o system prompt é actual (pode ter mudado entre deploys)
        stored_mode = row.get("Mode", "general")
        current_sp = get_userstory_system_prompt() if stored_mode == "userstory" else get_agent_system_prompt()

        # Substituir system prompt armazenado pelo actual
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": current_sp}
        else:
            messages.insert(0, {"role": "system", "content": current_sp})

        conversations[conv_id] = messages
        conversation_meta[conv_id] = {
            "mode": stored_mode,
            "created_at": row.get("CreatedAt", datetime.now().isoformat()),
            "loaded_from_storage": True,
        }

        logger.info("[Agent] Loaded conversation %s from storage (%d messages)", conv_id, len(messages))
        return True
    except Exception as e:
        logger.warning("[Agent] _load_conversation_from_storage failed for %s: %s", conv_id, e)
        return False


async def _ensure_conversation(conv_id: str, mode: str) -> str:
    """Garante que a conversa existe — tenta lazy-load do Table Storage se não estiver em memória."""
    conversations.cleanup_expired()
    if conv_id not in conversations:
        # Tentar carregar do Table Storage
        loaded = await _load_conversation_from_storage(conv_id)
        if not loaded:
            # Conversa nova
            sp = get_userstory_system_prompt() if mode == "userstory" else get_agent_system_prompt()
            conversations[conv_id] = [{"role": "system", "content": sp}]
            conversation_meta[conv_id] = {"mode": mode, "created_at": datetime.now().isoformat()}
    else:
        conversations.touch(conv_id)
    return conv_id


async def _build_llm_messages(conv_id: str, question: str) -> List[dict]:
    """Cópia efémera do histórico + regras + few-shot. Não muta conversations[]."""
    base = conversations[conv_id].copy()
    insert_pos = 1 if base else 0

    learned = await get_learned_rules()
    if learned:
        base.insert(insert_pos, {"role": "system", "content": learned})
        insert_pos += 1

    fewshot = await get_few_shot_examples(question)
    if fewshot:
        base.insert(insert_pos, {"role": "system", "content": fewshot})

    return base


async def _persist_conversation(conv_id: str) -> None:
    """Persiste conversa na tabela ChatHistory (fire-and-forget)."""
    try:
        msgs = conversations.get(conv_id)
        if not msgs:
            return

        # Comprimir para caber no limite de 64KB do Table Storage
        compact = []
        for m in msgs:
            if m.get("role") == "tool":
                # Truncar tool results para reduzir tamanho
                content = m.get("content", "")
                if len(content) > 500:
                    try:
                        data = json.loads(content)
                        summary = {
                            "total_count": data.get("total_count", "?"),
                            "items_returned": len(data.get("items", [])),
                            "_persisted_summary": True,
                        }
                        content = json.dumps(summary, ensure_ascii=False)
                    except (json.JSONDecodeError, TypeError):
                        content = content[:500] + "...(truncado)"
                compact.append({**m, "content": content})
            else:
                # User e assistant msgs: limitar conteúdo a 8000 chars
                c = m.get("content", "")
                if isinstance(c, str) and len(c) > 8000:
                    compact.append({**m, "content": c[:8000] + "...(truncado)"})
                else:
                    compact.append(m)

        messages_json = json.dumps(compact, ensure_ascii=False, default=str)

        # Verificar se cabe (Azure Table Storage: 64KB por propriedade)
        if len(messages_json.encode("utf-8")) > 60000:
            # Manter apenas as últimas N mensagens
            compact = [compact[0]] + compact[-10:]  # system + últimas 10
            messages_json = json.dumps(compact, ensure_ascii=False, default=str)

        meta = conversation_meta.get(conv_id, {})
        entity = {
            "PartitionKey": "chat",
            "RowKey": conv_id,
            "Messages": messages_json,
            "Mode": meta.get("mode", "general"),
            "CreatedAt": meta.get("created_at", datetime.now().isoformat()),
            "UpdatedAt": datetime.now().isoformat(),
            "MessageCount": len(compact),
        }

        existing = await table_query(
            "ChatHistory",
            f"PartitionKey eq 'chat' and RowKey eq '{conv_id}'",
            top=1,
        )
        if existing:
            await table_merge("ChatHistory", entity)
        else:
            inserted = await table_insert("ChatHistory", entity)
            if not inserted:
                await table_merge("ChatHistory", entity)
    except Exception as e:
        logger.warning("[Agent] _persist_conversation failed for %s: %s", conv_id, e)


# =============================================================================
# TOOL EXECUTION HELPER
# =============================================================================

async def _execute_tool_calls(
    tool_calls: List[LLMToolCall], conv_id: str,
) -> tuple[List[str], List[Dict]]:
    """Executa tools em paralelo, retorna (tools_used, tool_details)."""
    tools_used = []
    tool_details = []
    
    async def _run(tc: LLMToolCall):
        args = tc.arguments
        # Auto-inject file context for US generation
        file_data = uploaded_files_store.get(conv_id)
        if tc.name == "generate_user_stories" and file_data and not args.get("context"):
            args["context"] = file_data.get("data_text", "")[:20000]
        result = await execute_tool(tc.name, args)
        return tc, result
    
    results = await asyncio.gather(
        *[_run(tc) for tc in tool_calls],
        return_exceptions=True,
    )
    
    for res in results:
        if isinstance(res, Exception):
            print(f"Tool error: {res}")
            continue
        tc, tool_result = res
        tools_used.append(tc.name)
        
        td = {
            "tool": tc.name, "arguments": tc.arguments,
            "result_summary": {
                "total_count": tool_result.get("total_count", tool_result.get("total_results", tool_result.get("total_found", "N/A"))),
                "items_returned": len(tool_result.get("items", tool_result.get("analysis_data", []))),
                "has_error": "error" in tool_result,
            },
            "result_json": json.dumps(tool_result, ensure_ascii=False, default=str)[:30000],
        }
        tool_details.append(td)
        
        result_str = json.dumps(tool_result, ensure_ascii=False, default=str)
        result_str = truncate_tool_result(result_str)
        
        # Add tool result to conversation
        conversations[conv_id].append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result_str,
        })
    
    return tools_used, tool_details


# =============================================================================
# AGENT CHAT (request/response clássico)
# =============================================================================

async def agent_chat(request: AgentChatRequest, user: dict) -> AgentChatResponse:
    start = datetime.now()
    mode = request.mode or "general"
    tier = request.model_tier or LLM_DEFAULT_TIER
    conv_id = request.conversation_id or str(uuid.uuid4())
    
    await _ensure_conversation(conv_id, mode)
    _inject_file_context(conv_id, conversations[conv_id])
    
    conversations[conv_id].append(_build_user_message(request))
    conversations[conv_id] = _trim_history(conversations[conv_id])
    
    tools_used = []
    tool_details = []
    total_usage = {}
    model_used = ""
    has_exportable = False
    export_idx = None
    
    try:
        # Agent loop
        ephemeral = await _build_llm_messages(conv_id, request.question)
        response = await llm_with_fallback(
            ephemeral, tools=TOOLS, tier=tier,
            temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
        )
        model_used = response.model
        total_usage = response.usage
        
        iteration = 0
        while response.tool_calls and iteration < AGENT_MAX_ITERATIONS:
            iteration += 1
            
            # Add assistant message with tool calls to history
            conversations[conv_id].append(
                make_assistant_message_from_response(response)
            )
            
            # Execute tools
            tu, td = await _execute_tool_calls(response.tool_calls, conv_id)
            tools_used.extend(tu)
            tool_details.extend(td)
            
            # Check for exportable data
            for d in td:
                if d["result_summary"].get("items_returned", 0) > 0:
                    has_exportable = True
                    export_idx = len(tool_details) - 1
            
            # Next LLM call
            ephemeral = await _build_llm_messages(conv_id, request.question)
            response = await llm_with_fallback(
                ephemeral, tools=TOOLS, tier=tier,
                temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
            )
            for k, v in response.usage.items():
                if isinstance(v, (int, float)):
                    total_usage[k] = total_usage.get(k, 0) + v
        
        answer = response.content or "Não consegui processar a tua pergunta."
        conversations[conv_id].append({"role": "assistant", "content": answer})

        # Persist to Table Storage (fire-and-forget)
        asyncio.create_task(_persist_conversation(conv_id))
        
    except Exception as e:
        logger.error("[Agent] agent_chat exception: %s", e, exc_info=True)
        answer = f"Erro: {str(e)}"
    
    total_time = int((datetime.now() - start).total_seconds() * 1000)
    
    return AgentChatResponse(
        answer=answer,
        conversation_id=conv_id,
        tools_used=list(set(tools_used)),
        tool_details=tool_details,
        tokens_used=total_usage,
        total_time_ms=total_time,
        model_used=model_used,
        mode=mode,
        has_exportable_data=has_exportable,
        export_index=export_idx,
    )


# =============================================================================
# AGENT CHAT STREAM (SSE)
# =============================================================================

async def agent_chat_stream(request: AgentChatRequest, user: dict) -> AsyncGenerator[str, None]:
    """SSE streaming — yields 'data: {json}\n\n' strings."""
    start = datetime.now()
    mode = request.mode or "general"
    tier = request.model_tier or LLM_DEFAULT_TIER
    conv_id = request.conversation_id or str(uuid.uuid4())
    
    await _ensure_conversation(conv_id, mode)
    _inject_file_context(conv_id, conversations[conv_id])
    conversations[conv_id].append(_build_user_message(request))
    conversations[conv_id] = _trim_history(conversations[conv_id])
    
    tools_used = []
    tool_details = []
    total_usage = {}
    model_used = ""
    
    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    
    # Emit conversation_id immediately
    yield _sse({"type": "init", "conversation_id": conv_id, "mode": mode})
    
    try:
        provider = get_provider(tier)
        model_used = getattr(provider, 'model', getattr(provider, 'deployment', ''))
        
        iteration = 0
        need_final_response = True
        
        while iteration <= AGENT_MAX_ITERATIONS and need_final_response:
            iteration += 1
            
            yield _sse({"type": "thinking", "text": "A analisar..." if iteration == 1 else "A processar resultados..."})
            
            # Non-streaming call for tool detection
            ephemeral = await _build_llm_messages(conv_id, request.question)
            response = await llm_with_fallback(
                ephemeral, tools=TOOLS, tier=tier,
                temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
            )
            for k, v in response.usage.items():
                if isinstance(v, (int, float)):
                    total_usage[k] = total_usage.get(k, 0) + v
            
            if response.tool_calls:
                # Add assistant msg with tool calls
                conversations[conv_id].append(
                    make_assistant_message_from_response(response)
                )
                
                # Signal tool execution
                for tc in response.tool_calls:
                    yield _sse({"type": "tool_start", "tool": tc.name, "text": f"🔍 {tc.name}..."})
                
                # Execute tools
                tu, td = await _execute_tool_calls(response.tool_calls, conv_id)
                tools_used.extend(tu)
                tool_details.extend(td)
                
                for d in td:
                    count = d["result_summary"].get("total_count", d["result_summary"].get("items_returned", ""))
                    yield _sse({"type": "tool_result", "tool": d["tool"], "text": f"✅ {d['tool']}: {count} resultados"})
                
                # Continue loop for next LLM call
                need_final_response = True
            else:
                # Final text response — stream it
                need_final_response = False
                
                if response.content:
                    # Try to re-do as streaming for token-by-token delivery
                    # Remove tools so we get pure text streaming
                    try:
                        ephemeral_stream = await _build_llm_messages(conv_id, request.question)
                        async for event in provider.chat_stream(
                            ephemeral_stream, tools=None,
                            temperature=AGENT_TEMPERATURE, max_tokens=AGENT_MAX_TOKENS,
                        ):
                            if event.type == "token" and event.text:
                                yield _sse({"type": "token", "text": event.text})
                            elif event.type == "done":
                                break
                    except Exception as e:
                        logger.warning("[Agent] streaming failed, falling back to non-streaming: %s", e)
                        # Fallback: send full content at once
                        yield _sse({"type": "token", "text": response.content})
                    
                    conversations[conv_id].append({"role": "assistant", "content": response.content})

                    # Persist to Table Storage (fire-and-forget)
                    asyncio.create_task(_persist_conversation(conv_id))
                else:
                    yield _sse({"type": "token", "text": "Não consegui processar a tua pergunta."})
        
    except Exception as e:
        yield _sse({"type": "error", "text": str(e)})
    
    total_time = int((datetime.now() - start).total_seconds() * 1000)
    
    yield _sse({
        "type": "done",
        "tools_used": list(set(tools_used)),
        "tool_details": tool_details,
        "tokens_used": total_usage,
        "total_time_ms": total_time,
        "model_used": model_used,
        "conversation_id": conv_id,
    })


# =============================================================================
# MODE SWITCHING
# =============================================================================

def switch_conversation_mode(conv_id: str, new_mode: str) -> bool:
    """Muda o modo de uma conversa existente. Reinjecta system prompt."""
    if conv_id not in conversations:
        return False
    if new_mode not in ("general", "userstory"):
        return False
    
    sp = get_userstory_system_prompt() if new_mode == "userstory" else get_agent_system_prompt()
    
    # Replace first system message
    new_msgs = [{"role": "system", "content": sp}]
    new_msgs.extend(m for m in conversations[conv_id] if m.get("role") != "system")
    conversations[conv_id] = new_msgs
    conversation_meta.setdefault(conv_id, {})["mode"] = new_mode
    
    return True
