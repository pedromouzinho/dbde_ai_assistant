# =============================================================================
# app.py — FastAPI routes + wiring v7.0
# =============================================================================
# Thin routing layer: liga todos os módulos, expõe endpoints.
# Nenhuma lógica de negócio aqui — apenas routing e error handling.
# =============================================================================

import io
import json
import uuid
import asyncio
import base64
import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import (
    APP_VERSION, APP_TITLE, APP_DESCRIPTION,
    DEVOPS_INDEX, OMNI_INDEX, EXAMPLES_INDEX,
    SEARCH_SERVICE, SEARCH_KEY, API_VERSION_SEARCH,
    AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, CHAT_DEPLOYMENT, API_VERSION_CHAT,
    LLM_TIER_FAST, LLM_TIER_STANDARD, LLM_TIER_PRO,
)
from models import (
    AgentChatRequest, AgentChatResponse,
    LoginRequest, CreateUserRequest, ChangePasswordRequest,
    FeedbackRequest, ExportRequest, SaveChatRequest,
    ModeSwitchRequest, ModeSwitchResponse,
)
from auth import get_current_user, jwt_encode, hash_password, verify_password
from storage import (
    init_http_client, ensure_tables_exist,
    table_insert, table_query, table_merge, table_delete,
)
from tools import get_embedding, get_devops_debug_log, TOOLS
from learning import invalidate_prompt_rules_cache
from agent import (
    agent_chat as _agent_chat, agent_chat_stream,
    conversations, conversation_meta, uploaded_files_store,
    switch_conversation_mode,
)
from export_engine import to_csv, to_xlsx, to_pdf, to_svg_bar_chart, to_html_report
from llm_provider import llm_simple, get_debug_log as get_llm_debug_log

# =============================================================================
# APP SETUP
# =============================================================================

security = HTTPBearer()

app = FastAPI(title=APP_TITLE, version=APP_VERSION, description=APP_DESCRIPTION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

http_client: Optional[httpx.AsyncClient] = None
logger = logging.getLogger(__name__)

@app.on_event("startup")
async def startup_event():
    global http_client
    http_client = httpx.AsyncClient(timeout=60)
    init_http_client(http_client)
    print("🌐 HTTP client OK")
    try:
        await asyncio.wait_for(ensure_tables_exist(), timeout=15)
    except asyncio.TimeoutError:
        print("⚠️ Table init timeout (15s) — continuing anyway")
    except Exception as e:
        print(f"⚠️ Table init error: {e} — continuing anyway")
    print(f"✅ DBDE AI Agent v{APP_VERSION} ready")

@app.on_event("shutdown")
async def shutdown_event():
    if http_client:
        await http_client.aclose()

# =============================================================================
# LEARNING / FEW-SHOT HELPERS
# =============================================================================
feedback_memory = []

async def _index_example(example_id, question, answer, rating, tools_used=None, feedback_note="", example_type="positive"):
    try:
        emb = await get_embedding(question)
        if not emb: return
        doc = {"id":example_id,"question":question[:2000],"answer":answer[:4000],"tools_used":",".join(tools_used) if tools_used else "","rating":rating,"feedback_note":feedback_note[:500],"example_type":example_type,"created_at":datetime.utcnow().isoformat(),"question_vector":emb}
        url = f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{EXAMPLES_INDEX}/docs/index?api-version={API_VERSION_SEARCH}"
        async with httpx.AsyncClient(timeout=30) as c:
            await c.post(url, json={"value":[{"@search.action":"mergeOrUpload",**doc}]}, headers={"api-key":SEARCH_KEY,"Content-Type":"application/json"})
    except Exception as e:
        logger.error("[App] _index_example failed: %s", e)

async def log_audit(user_id, action, question="", tools_used=None, tokens=None, duration_ms=0):
    try:
        ts = datetime.utcnow()
        await table_insert("AuditLog", {"PartitionKey":ts.strftime("%Y-%m"),"RowKey":f"{ts.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}","UserId":user_id or "anon","Action":action,"Question":(question or "")[:500],"ToolsUsed":",".join(tools_used) if tools_used else "","TotalTokens":tokens.get("total_tokens",0) if tokens else 0,"DurationMs":duration_ms,"Timestamp":ts.isoformat()})
    except Exception as e:
        logger.error("[App] log_audit failed: %s", e)

# =============================================================================
# AGENT ENDPOINTS
# =============================================================================

@app.post("/chat/agent", response_model=AgentChatResponse)
async def agent_chat_endpoint(request: AgentChatRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)

    result = await _agent_chat(request, user)
    
    # Audit
    try: await log_audit(user.get("sub"), "agent_chat", request.question, result.tools_used, result.tokens_used, result.total_time_ms)
    except Exception as e:
        logger.error("[App] log_audit in chat failed: %s", e)
    
    return result

@app.post("/chat/agent/stream")
async def agent_chat_stream_endpoint(request: AgentChatRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """SSE streaming endpoint."""
    user = get_current_user(credentials)
    return StreamingResponse(agent_chat_stream(request, user), media_type="text/event-stream")

@app.post("/chat/file", response_model=AgentChatResponse)
async def chat_with_file(request: AgentChatRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Backward compat."""
    return await agent_chat_endpoint(request, credentials)

# =============================================================================
# MODE SWITCH
# =============================================================================

@app.post("/api/mode/switch", response_model=ModeSwitchResponse)
async def switch_mode(request: ModeSwitchRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    get_current_user(credentials)
    ok = switch_conversation_mode(request.conversation_id, request.mode)
    return ModeSwitchResponse(
        success=ok,
        message=f"Modo alterado para {request.mode}" if ok else "Conversa não encontrada",
        mode=request.mode,
        conversation_id=request.conversation_id,
    )

# =============================================================================
# FILE UPLOAD
# =============================================================================

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), conversation_id: Optional[str] = Form(None), credentials: HTTPAuthorizationCredentials = Depends(security)):
    get_current_user(credentials)
    conv_id = conversation_id or str(uuid.uuid4())
    content = await file.read()
    filename = file.filename or "unknown"
    if len(content) > 10*1024*1024: raise HTTPException(400, "Max 10MB")
    
    try:
        data_text, row_count, col_names, truncated = "", 0, [], False
        
        if filename.endswith((".xlsx",".xls")):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            rows = list(wb.active.iter_rows(values_only=True))
            if not rows: raise HTTPException(400, "Excel vazio")
            col_names = [str(c) if c else f"Col{i}" for i,c in enumerate(rows[0])]
            row_count = len(rows)-1
            data_text = "\t".join(col_names)+"\n" + "\n".join("\t".join(str(c) if c is not None else "" for c in r) for r in rows[1:])
            wb.close()
        elif filename.endswith(".csv"):
            text = content.decode("utf-8", errors="replace")
            lines = text.strip().split("\n")
            sep = "," if "," in lines[0] else ";"
            col_names = [c.strip().strip('"') for c in lines[0].split(sep)]
            row_count = len(lines)-1; data_text = text
        elif filename.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader
            except ImportError:
                from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages = [f"[Pág {i+1}]\n{p.extract_text() or ''}" for i,p in enumerate(reader.pages) if (p.extract_text() or "").strip()]
            data_text = "\n\n".join(pages); row_count = len(reader.pages); col_names = [f"páginas ({row_count})"]
            if not data_text.strip(): raise HTTPException(400, "PDF sem texto")
        elif filename.lower().endswith(".svg"):
            data_text = content.decode("utf-8", errors="replace")
            row_count = 0
            col_names = ["svg"]
        else:
            data_text = content.decode("utf-8", errors="replace"); row_count = data_text.count("\n"); col_names = ["texto"]
        
        if len(data_text) > 100000: data_text = data_text[:100000]; truncated = True
        
        uploaded_files_store[conv_id] = {"filename":filename,"data_text":data_text,"row_count":row_count,"col_names":col_names,"truncated":truncated,"uploaded_at":datetime.now().isoformat()}
        if conv_id in conversation_meta: conversation_meta[conv_id]["file_injected"] = False
        
        return {"status":"ok","conversation_id":conv_id,"filename":filename,"rows":row_count,"columns":col_names,"truncated":truncated,"preview":"\n".join(data_text.split("\n")[:6])}
    except HTTPException: raise
    except Exception as e: raise HTTPException(400, str(e))

# =============================================================================
# EXPORT ENDPOINTS (Fase 3)
# =============================================================================

@app.post("/api/export")
async def export_data(request: ExportRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Exporta dados de tool results em vários formatos."""
    get_current_user(credentials)
    
    data = None
    
    # Option 1: Direct data from frontend (v7.0.1)
    if request.data:
        data = request.data
    else:
        # Option 2: From server-side conversation memory
        conv_id = request.conversation_id
        if not conv_id or conv_id not in conversations:
            raise HTTPException(400, "Conversa não encontrada no servidor. Tenta exportar novamente após enviar uma mensagem.")
        
        tool_msgs = [m for m in conversations[conv_id] if m.get("role") == "tool"]
        idx = request.tool_call_index if request.tool_call_index is not None else -1
        if abs(idx) > len(tool_msgs):
            raise HTTPException(400, "Tool result não encontrado")
        
        try:
            data = json.loads(tool_msgs[idx]["content"])
        except (json.JSONDecodeError, IndexError):
            raise HTTPException(400, "Dados inválidos")
    
    if not data:
        raise HTTPException(400, "Sem dados para exportar")
    
    title = request.title or "Export DBDE"
    safe_filename = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)[:30] or "Export"
    fmt = request.format
    
    if fmt == "csv":
        buf = to_csv(data)
        return StreamingResponse(buf, media_type="text/csv", headers={"Content-Disposition":f'attachment; filename="{safe_filename}.csv"'})
    elif fmt == "xlsx":
        buf = to_xlsx(data, title)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition":f'attachment; filename="{safe_filename}.xlsx"'})
    elif fmt == "pdf":
        buf = to_pdf(data, title, request.summary or "")
        return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition":f'attachment; filename="{safe_filename}.pdf"'})
    elif fmt == "svg":
        svg = to_svg_bar_chart(data, title)
        return Response(content=svg, media_type="image/svg+xml", headers={"Content-Disposition":f'attachment; filename="{safe_filename}.svg"'})
    elif fmt == "html":
        html = to_html_report(data, title, request.summary or "")
        return HTMLResponse(content=html, headers={"Content-Disposition":f'attachment; filename="{safe_filename}.html"'})
    else:
        raise HTTPException(400, f"Formato não suportado: {fmt}")

# =============================================================================
# AUTH ENDPOINTS
# =============================================================================

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    users = await table_query("Users", f"PartitionKey eq 'user' and RowKey eq '{request.username}'", top=1)
    if not users: raise HTTPException(401, "Credenciais inválidas")
    user = users[0]
    if not verify_password(request.password, user.get("PasswordHash","")): raise HTTPException(401, "Credenciais inválidas")
    if user.get("IsActive") == False: raise HTTPException(403, "Conta desactivada")
    token = jwt_encode({"sub":request.username, "role":user.get("Role","user"), "name":user.get("DisplayName",request.username)})
    return {"access_token":token, "token_type":"bearer", "username":request.username, "role":user.get("Role","user"), "display_name":user.get("DisplayName",request.username)}

@app.post("/api/auth/create-user")
async def create_user(request: CreateUserRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Apenas admins")
    existing = await table_query("Users", f"PartitionKey eq 'user' and RowKey eq '{request.username}'", top=1)
    if existing: raise HTTPException(409, "Username já existe")
    entity = {"PartitionKey":"user","RowKey":request.username,"PasswordHash":hash_password(request.password),"DisplayName":request.display_name or request.username,"Role":request.role or "user","IsActive":True,"CreatedAt":datetime.utcnow().isoformat(),"CreatedBy":user.get("sub")}
    await table_insert("Users", entity)
    return {"status":"ok","username":request.username}

@app.get("/api/auth/users")
async def list_users(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Apenas admins")
    users = await table_query("Users", "PartitionKey eq 'user'", top=100)
    return {"users":[{"username":u.get("RowKey"),"display_name":u.get("DisplayName"),"role":u.get("Role"),"is_active":u.get("IsActive",True)} for u in users]}

@app.delete("/api/auth/users/{username}")
async def deactivate_user(username: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Apenas admins")
    if username == user.get("sub"): raise HTTPException(400, "Não podes desactivar-te")
    await table_merge("Users", {"PartitionKey":"user","RowKey":username,"IsActive":False})
    return {"status":"ok"}

@app.post("/api/auth/change-password")
async def change_password(request: ChangePasswordRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    username = user.get("sub")
    users = await table_query("Users", f"PartitionKey eq 'user' and RowKey eq '{username}'", top=1)
    if not users: raise HTTPException(404, "User não encontrado")
    if not verify_password(request.current_password, users[0].get("PasswordHash","")): raise HTTPException(401, "Password actual incorrecta")
    await table_merge("Users", {"PartitionKey":"user","RowKey":username,"PasswordHash":hash_password(request.new_password)})
    return {"status":"ok"}

@app.post("/api/auth/reset-password/{username}")
async def admin_reset_password(username: str, request: LoginRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Apenas admins")
    await table_merge("Users", {"PartitionKey":"user","RowKey":username,"PasswordHash":hash_password(request.password)})
    return {"status":"ok"}

@app.get("/api/auth/me")
async def get_me(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    return {"username":user.get("sub"),"role":user.get("role"),"name":user.get("name")}

# =============================================================================
# FEEDBACK
# =============================================================================

@app.post("/feedback")
async def submit_feedback(request: FeedbackRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    get_current_user(credentials)
    question, answer = "", ""
    cid = request.conversation_id
    if cid in conversations:
        um = [m for m in conversations[cid] if m.get("role")=="user"]
        am = [m for m in conversations[cid] if m.get("role")=="assistant"]
        if um: question = um[-1].get("content","") if isinstance(um[-1].get("content"),str) else str(um[-1].get("content",""))
        if am: answer = am[-1].get("content","")
    
    ts = datetime.utcnow().isoformat()
    safe_conv = cid.replace("-","")[:32]
    entity = {"PartitionKey":safe_conv,"RowKey":f"{request.message_index}_{ts.replace(':','').replace('-','').replace('.','')}", "Rating":request.rating,"Note":request.note or "","Question":question[:2000],"Answer":answer[:4000],"Timestamp_str":ts}
    stored = await table_insert("feedback", entity)
    if not stored: feedback_memory.append(entity)
    
    if question and answer and (request.rating >= 7 or request.rating <= 3):
        etype = "positive" if request.rating >= 7 else "negative"
        eid = f"{safe_conv}_{request.message_index}"
        await table_insert("examples", {"PartitionKey":etype,"RowKey":eid,"Question":question[:2000],"Answer":answer[:4000],"Rating":request.rating,"Note":request.note or "","Timestamp_str":ts})
        try: await _index_example(eid, question, answer, request.rating, example_type=etype, feedback_note=request.note or "")
        except Exception as e:
            logger.error("[App] _index_example in feedback failed: %s", e)
    
    return {"status":"ok","message":f"Feedback: {request.rating}/10","persisted":"table_storage" if stored else "memory"}

@app.get("/feedback/stats")
async def feedback_stats(credentials: HTTPAuthorizationCredentials = Depends(security)):
    get_current_user(credentials)
    fbs = await table_query("feedback", top=1000)
    all_fb = fbs + feedback_memory
    if not all_fb: return {"total":0,"average_rating":0}
    ratings = [f.get("Rating",0) for f in all_fb if f.get("Rating",0)>0]
    if not ratings: return {"total":0,"average_rating":0}
    return {"total":len(ratings),"average_rating":round(sum(ratings)/len(ratings),1),"distribution":{str(r):ratings.count(r) for r in range(1,11)}}

# =============================================================================
# CHAT PERSISTENCE
# =============================================================================

@app.post("/api/chats/save")
async def save_chat(request: SaveChatRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    uid = user.get("sub", request.user_id)
    msgs = [{"role":m.get("role",""),"content":m.get("content","")} for m in request.messages]
    msgs_json = json.dumps(msgs, ensure_ascii=False)
    while len(msgs_json)>60000 and len(msgs)>4: msgs.pop(1); msgs_json = json.dumps(msgs, ensure_ascii=False)
    entity = {"PartitionKey":uid,"RowKey":request.conversation_id,"Title":(request.title or "Nova conversa")[:100],"Messages":msgs_json,"MessageCount":len(request.messages),"UpdatedAt":datetime.utcnow().isoformat()}
    if not await table_insert("ChatHistory", entity): await table_merge("ChatHistory", entity)
    return {"status":"ok","conversation_id":request.conversation_id}

@app.get("/api/chats/{user_id}")
async def list_chats(user_id: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    uid = user.get("sub") if user.get("role")!="admin" else user_id
    entities = await table_query("ChatHistory", f"PartitionKey eq '{uid}'", top=100)
    chats = sorted([{"conversation_id":e.get("RowKey",""),"title":e.get("Title",""),"message_count":e.get("MessageCount",0),"updated_at":e.get("UpdatedAt","")} for e in entities], key=lambda c:c["updated_at"], reverse=True)
    return {"chats":chats}

@app.get("/api/chats/{user_id}/{conversation_id}")
async def get_chat(user_id: str, conversation_id: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    uid = user.get("sub") if user.get("role")!="admin" else user_id
    es = await table_query("ChatHistory", f"PartitionKey eq '{uid}' and RowKey eq '{conversation_id}'", top=1)
    if not es: raise HTTPException(404, "Não encontrada")
    return {"conversation_id":conversation_id,"title":es[0].get("Title",""),"messages":json.loads(es[0].get("Messages","[]")),"updated_at":es[0].get("UpdatedAt","")}

@app.delete("/api/chats/{user_id}/{conversation_id}")
async def delete_chat(user_id: str, conversation_id: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    uid = user.get("sub") if user.get("role")!="admin" else user_id
    await table_delete("ChatHistory", uid, conversation_id)
    return {"status":"ok"}

# =============================================================================
# LEARNING ENDPOINTS
# =============================================================================

@app.post("/api/learning/rules")
async def add_rule(rule_text: str, category: str = "general", credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Admin only")
    rid = f"rule_{uuid.uuid4().hex[:8]}"
    await table_insert("PromptRules", {"PartitionKey":"active","RowKey":rid,"RuleText":rule_text,"Category":category,"CreatedBy":user.get("sub"),"CreatedAt":datetime.utcnow().isoformat()})
    invalidate_prompt_rules_cache()
    return {"status":"ok","rule_id":rid}

@app.get("/api/learning/rules")
async def list_rules(credentials: HTTPAuthorizationCredentials = Depends(security)):
    get_current_user(credentials)
    rules = await table_query("PromptRules", "PartitionKey eq 'active'", top=50)
    return {"rules":[{"id":r.get("RowKey"),"text":r.get("RuleText"),"category":r.get("Category"),"created_by":r.get("CreatedBy")} for r in rules]}

@app.delete("/api/learning/rules/{rule_id}")
async def delete_rule(rule_id: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Admin only")
    await table_delete("PromptRules", "active", rule_id)
    invalidate_prompt_rules_cache()
    return {"status":"ok"}

@app.post("/api/learning/analyze")
async def analyze_feedback(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403, "Admin only")
    fbs = await table_query("feedback", top=500)
    if not fbs: return {"analysis":"Sem feedback suficiente.","suggestions":[]}
    
    neg = [f for f in fbs if f.get("Rating",10) <= 3]
    pos = [f for f in fbs if f.get("Rating",0) >= 8]
    summary = f"Total: {len(fbs)} feedbacks. Positivos(8+): {len(pos)}. Negativos(3-): {len(neg)}.\n\n"
    if neg:
        summary += "FEEDBACK NEGATIVO:\n"
        for f in neg[:10]: summary += f"- Q: {f.get('Question','')[:80]}... Rating: {f.get('Rating')}, Nota: {f.get('Note','')}\n"
    
    try:
        analysis = await llm_simple(f"Analisa feedback de agente AI e sugere melhorias:\n\n{summary}", tier="fast", max_tokens=1500)
    except Exception as e:
        logger.warning("[App] analyze_feedback LLM failed, using summary fallback: %s", e)
        analysis = summary
    
    return {"analysis":analysis, "total":len(fbs), "positive":len(pos), "negative":len(neg)}

# =============================================================================
# INFO / HEALTH / DEBUG
# =============================================================================

@app.get("/api/info")
async def api_info():
    return {
        "service": APP_TITLE, "version": APP_VERSION, "status": "running",
        "models": {"fast": LLM_TIER_FAST, "standard": LLM_TIER_STANDARD, "pro": LLM_TIER_PRO},
        "indexes": {"devops": DEVOPS_INDEX, "omni": OMNI_INDEX, "examples": EXAMPLES_INDEX},
        "capabilities": ["multi_model","streaming_sse","jwt_auth","agent_routing","parallel_tools","export_csv_xlsx_pdf_svg_html","feedback","file_upload","chat_persistence","adaptive_learning"],
    }

@app.get("/health")
async def health():
    checks = {}
    try:
        emb = await get_embedding("test")
        checks["embeddings"] = "ok" if emb else "error"
    except Exception as e: checks["embeddings"] = f"error: {str(e)[:80]}"
    
    for name, idx in [("devops", DEVOPS_INDEX), ("omni", OMNI_INDEX)]:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{idx}/docs/$count?api-version={API_VERSION_SEARCH}", headers={"api-key":SEARCH_KEY})
                checks[f"search_{name}"] = f"ok ({r.text} docs)" if r.status_code==200 else f"error: {r.status_code}"
        except Exception as e: checks[f"search_{name}"] = f"error: {str(e)[:80]}"
    
    checks["devops_log"] = get_devops_debug_log()[-5:]
    checks["llm_log"] = get_llm_debug_log()[-5:]
    
    return {"status": "healthy" if all("ok" in str(v) for k,v in checks.items() if "log" not in k) else "degraded", "checks": checks}

@app.get("/debug/conversations")
async def debug_conversations(credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin": raise HTTPException(403)
    return {cid: {"mode":conversation_meta.get(cid,{}).get("mode"), "msgs":len(msgs), "has_file":cid in uploaded_files_store} for cid,msgs in conversations.items()}

# =============================================================================
# FRONTEND
# =============================================================================

@app.get("/")
async def root():
    try:
        with open("/home/site/wwwroot/static/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content=f"<h1>{APP_TITLE} v{APP_VERSION}</h1><p>Frontend not deployed. Use /docs for API.</p>")
