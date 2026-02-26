# =============================================================================
# tools_devops.py — DevOps tooling and LLM-assisted story generation
# =============================================================================

import json
import base64
import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote
from typing import Optional
from collections import deque

from config import (
    DEVOPS_PAT,
    DEVOPS_ORG,
    DEVOPS_PROJECT,
    DEVOPS_INDEX,
    DEVOPS_FIELDS,
    DEVOPS_AREAS,
    DEVOPS_WORKITEM_TYPES,
    DEBUG_LOG_SIZE,
    AGENT_TOOL_RESULT_MAX_SIZE,
    AGENT_TOOL_RESULT_KEEP_ITEMS,
    EXPORT_ASYNC_THRESHOLD_ROWS,
)
from llm_provider import llm_simple
from export_engine import to_csv
from http_helpers import devops_request_with_retry
from tools_knowledge import get_embedding
from tools_export import _attach_auto_csv_export
from tools_learning import _save_writer_profile, _load_writer_profile

_devops_debug_log: deque = deque(maxlen=DEBUG_LOG_SIZE)

def get_devops_debug_log(): return list(_devops_debug_log)

def _log(msg):
    _devops_debug_log.append({"ts": datetime.now(timezone.utc).isoformat(), "msg": msg})
    logging.info("[Tools] %s", msg)

_WIQL_BLOCKLIST_RE = re.compile(
    r"(?i)(;|--|/\*|\*/|\b(select|drop|delete|update|insert|merge|exec|execute|union)\b)"
)

_WORKITEM_TYPE_MAP = {str(t).strip().lower(): str(t).strip() for t in DEVOPS_WORKITEM_TYPES}

def _devops_headers():
    return {"Authorization": f"Basic {base64.b64encode(f':{DEVOPS_PAT}'.encode()).decode()}", "Content-Type": "application/json"}

def _devops_url(path):
    return f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_apis/{path}"

def _format_wi(item):
    f = item.get("fields", {})
    a = f.get("System.AssignedTo", {}); c = f.get("System.CreatedBy", {})
    result = {
        "id": item["id"], "type": f.get("System.WorkItemType",""),
        "title": f.get("System.Title","").replace(" | "," — "), "state": f.get("System.State",""),
        "area": f.get("System.AreaPath",""),
        "assigned_to": a.get("displayName","") if isinstance(a,dict) else str(a),
        "created_by": c.get("displayName","") if isinstance(c,dict) else str(c),
        "created_date": f.get("System.CreatedDate",""),
        "url": f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{item['id']}",
    }
    # Include extra fields when present (Description, AcceptanceCriteria, Tags)
    desc = f.get("System.Description", "")
    ac = f.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
    tags = f.get("System.Tags", "")
    if desc: result["description"] = (desc or "")[:3000]
    if ac: result["acceptance_criteria"] = (ac or "")[:3000]
    if tags: result["tags"] = tags
    return result

def _safe_wiql_literal(value: str, max_len: int = 200) -> str:
    text = str(value or "").strip()
    if max_len > 0:
        text = text[:max_len]
    return text.replace("'", "''")

def _normalize_match_text(value: str) -> str:
    lowered = str(value or "").lower()
    deaccented = unicodedata.normalize("NFKD", lowered)
    clean = "".join(ch for ch in deaccented if not unicodedata.combining(ch))
    clean = clean.replace("|", " ").replace("—", " ").replace("-", " ").replace("_", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def _canonicalize_area_path(area_path: str) -> str:
    raw = str(area_path or "").strip()
    if not raw:
        return ""
    if "\\" in raw:
        return raw
    norm = _normalize_match_text(raw)
    if not norm:
        return raw
    for known in DEVOPS_AREAS:
        known_norm = _normalize_match_text(known)
        if known_norm.endswith(norm) or norm in known_norm:
            return known
    return raw

def _sanitize_wiql_where(wiql_where: str) -> str:
    where = str(wiql_where or "").strip()
    if where.lower().startswith("where "):
        where = where[6:].strip()
    if not where:
        raise ValueError("wiql_where vazio")
    if len(where) > 2000:
        raise ValueError("wiql_where demasiado longo (max 2000 chars)")
    if _WIQL_BLOCKLIST_RE.search(where):
        raise ValueError("wiql_where contém tokens proibidos")
    if where.count("'") % 2 != 0:
        raise ValueError("wiql_where com aspas simples não balanceadas")
    return where


def _clean_html_for_example(html_text: str) -> str:
    """Remove HTML sujo dos exemplos, mantendo apenas tags limpas."""
    text = str(html_text or "")
    if not text:
        return ""
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", text)

    allowed_tags = {"b", "ul", "li", "br", "div"}

    def _normalize_tag(match):
        closing = bool(match.group(1))
        tag = str(match.group(2) or "").lower()
        if tag not in allowed_tags:
            return ""
        if tag == "br":
            return "<br>"
        return f"</{tag}>" if closing else f"<{tag}>"

    text = re.sub(r"<\s*(/?)\s*([a-zA-Z0-9]+)([^>]*)>", _normalize_tag, text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()

def _extract_json_object(text: str):
    if not isinstance(text, str):
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start:end + 1]
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except Exception:
        return None

def _validate_workitem_type(value: str, default: str = "User Story") -> str:
    candidate = str(value or default).strip().lower()
    safe = _WORKITEM_TYPE_MAP.get(candidate)
    if not safe:
        raise ValueError(
            f"Tipo de work item inválido: '{value}'. Permitidos: {', '.join(DEVOPS_WORKITEM_TYPES)}"
        )
    return safe

async def _resolve_parent_id_by_title_hint(
    headers: dict,
    *,
    parent_type: str,
    area_path: str = "",
    title_hint: str = "",
) -> tuple[Optional[int], dict]:
    hint_raw = str(title_hint or "").strip()
    hint_norm = _normalize_match_text(hint_raw)
    score_terms = [t for t in hint_norm.split(" ") if t][:8]
    wiql_terms_src = re.sub(r"[|—\\-_]", " ", hint_raw)
    wiql_terms_src = re.sub(r"\s+", " ", wiql_terms_src).strip()
    wiql_terms = [t for t in wiql_terms_src.split(" ") if t][:8]
    if not wiql_terms:
        wiql_terms = score_terms[:]
    if not score_terms:
        score_terms = [_normalize_match_text(t) for t in wiql_terms]
        score_terms = [t for t in score_terms if t][:8]
    if not score_terms:
        return None, {"attempted": False}

    parent_type_norm = str(parent_type or "").strip().lower()
    apply_area_filter = bool(area_path and parent_type_norm != "epic")
    base_conds = [
        f"[System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}'",
        f"[System.WorkItemType] = '{_safe_wiql_literal(parent_type, 80)}'",
    ]
    if apply_area_filter:
        base_conds.append(f"[System.AreaPath] UNDER '{_safe_wiql_literal(area_path, 300)}'")
    strict_conds = list(base_conds)
    for term in wiql_terms:
        strict_conds.append(f"[System.Title] CONTAINS '{_safe_wiql_literal(term, 80)}'")

    wiql = (
        "SELECT [System.Id] FROM WorkItems "
        f"WHERE {' AND '.join(strict_conds)} "
        "ORDER BY [System.ChangedDate] DESC"
    )
    resp = await devops_request_with_retry(
        "POST",
        _devops_url("wit/wiql?api-version=7.1"),
        headers,
        {"query": wiql},
        timeout=60,
    )
    if "error" in resp:
        return None, {
            "attempted": True,
            "area_filter_applied": apply_area_filter,
            "error": resp.get("error", "resolve_parent_failed"),
            "wiql_terms": wiql_terms,
        }

    ids = [wi.get("id") for wi in resp.get("workItems", []) if wi.get("id")]
    fallback_broad_used = False
    if not ids and wiql_terms:
        fallback_wiql = (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE {' AND '.join(base_conds)} "
            "ORDER BY [System.ChangedDate] DESC"
        )
        fallback_resp = await devops_request_with_retry(
            "POST",
            _devops_url("wit/wiql?api-version=7.1"),
            headers,
            {"query": fallback_wiql},
            timeout=60,
        )
        if "error" not in fallback_resp:
            ids = [wi.get("id") for wi in fallback_resp.get("workItems", []) if wi.get("id")]
            fallback_broad_used = True

    if not ids:
        return None, {
            "attempted": True,
            "area_filter_applied": apply_area_filter,
            "matched_candidates": 0,
            "wiql_terms": wiql_terms,
            "fallback_broad_used": fallback_broad_used,
        }

    batch_ids = ids[: min(50, len(ids))]
    det = await devops_request_with_retry(
        "POST",
        _devops_url("wit/workitemsbatch?api-version=7.1"),
        headers,
        {"ids": batch_ids, "fields": ["System.Id", "System.Title", "System.WorkItemType", "System.AreaPath"]},
        timeout=60,
    )
    if "error" in det:
        return None, {
            "attempted": True,
            "area_filter_applied": apply_area_filter,
            "matched_candidates": len(ids),
            "error": det.get("error", "resolve_parent_batch_failed"),
            "wiql_terms": wiql_terms,
            "fallback_broad_used": fallback_broad_used,
        }

    best_id = None
    best_score = -1
    exact_hits = 0
    exact_title_hits = 0
    for it in det.get("value", []):
        f = it.get("fields", {})
        title_norm = _normalize_match_text(str(f.get("System.Title", "") or ""))
        score = sum(1 for term in score_terms if term in title_norm)
        if score_terms and score == len(score_terms):
            exact_hits += 1
        if hint_norm and title_norm == hint_norm:
            exact_title_hits += 1
            score += 100
        elif hint_norm and title_norm.startswith(hint_norm):
            score += 20
        if score > best_score:
            best_score = score
            best_id = it.get("id")

    if best_id is None:
        return None, {
            "attempted": True,
            "area_filter_applied": apply_area_filter,
            "matched_candidates": len(ids),
            "scored_candidates": len(det.get("value", [])),
            "wiql_terms": wiql_terms,
            "fallback_broad_used": fallback_broad_used,
        }
    return int(best_id), {
        "attempted": True,
        "area_filter_applied": apply_area_filter,
        "matched_candidates": len(ids),
        "scored_candidates": len(det.get("value", [])),
        "best_score": best_score,
        "max_score": len(score_terms),
        "exact_hits": exact_hits,
        "exact_title_hits": exact_title_hits,
        "wiql_terms": wiql_terms,
        "fallback_broad_used": fallback_broad_used,
    }

async def tool_query_workitems(wiql_where, fields=None, top=200):
    _log(f"query_workitems: top={top}, wiql={str(wiql_where)[:80]}...")
    try:
        safe_where = _sanitize_wiql_where(wiql_where)
    except ValueError as e:
        return {"error": f"WIQL inválido: {e}"}
    use_fields = fields if fields and len(fields) > 0 else DEVOPS_FIELDS
    wiql = (
        "SELECT [System.Id] FROM WorkItems "
        f"WHERE [System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}' "
        f"AND {safe_where} ORDER BY [System.ChangedDate] DESC"
    )
    headers = _devops_headers()
    resp = await devops_request_with_retry(
        "POST",
        _devops_url("wit/wiql?api-version=7.1"),
        headers,
        {"query": wiql},
        timeout=60,
    )
    if "error" in resp:
        return resp
    work_items = resp.get("workItems", [])
    total_count = len(work_items)
    if top == 0:
        return {"total_count": total_count, "items": []}
    work_items = work_items[: min(top, 1000) if top > 0 else total_count]
    if not work_items:
        return {"total_count": 0, "items": []}
    await asyncio.sleep(0.5)
    all_details, failed_ids, ids = [], [], [wi["id"] for wi in work_items]
    for i in range(0, len(ids), 100):
        batch = ids[i : i + 100]
        r = await devops_request_with_retry(
            "POST",
            _devops_url("wit/workitemsbatch?api-version=7.1"),
            headers,
            {"ids": batch, "fields": use_fields},
            timeout=60,
        )
        if "error" in r:
            failed_ids.extend(batch)
            await asyncio.sleep(3)
            continue
        all_details.extend(r.get("value", []))
        await asyncio.sleep(0.5)
    if failed_ids and len(failed_ids) <= 50:
        await asyncio.sleep(2)
        fl = ",".join(use_fields)
        for fid in failed_ids[:]:
            r = await devops_request_with_retry(
                "GET",
                _devops_url(f"wit/workitems/{fid}?fields={fl}&api-version=7.1"),
                headers,
                max_retries=3,
                timeout=60,
            )
            if "error" not in r and "id" in r:
                all_details.append(r)
                failed_ids.remove(fid)
            await asyncio.sleep(0.3)
    items = [_format_wi(it) for it in all_details]
    if failed_ids and not items:
        items = [
            {
                "id": fid,
                "type": "",
                "title": "(rate limited)",
                "state": "",
                "url": f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{fid}",
            }
            for fid in failed_ids
        ]
    result = {"total_count": total_count, "items_returned": len(items), "items": items}
    await _attach_auto_csv_export(
        result,
        title_hint=f"query_workitems_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}",
    )
    if failed_ids:
        result["_partial"] = True
        result["_failed_batch_count"] = len(failed_ids)
    return result

async def tool_analyze_patterns(created_by=None, topic=None, work_item_type="User Story", area_path=None, sample_size=15):
    try:
        safe_type = _validate_workitem_type(work_item_type, "User Story")
    except ValueError as e:
        return {"error": str(e)}

    conds = [f"[System.WorkItemType]='{_safe_wiql_literal(safe_type, 80)}'"]
    if created_by:
        conds.append(f"[System.CreatedBy] CONTAINS '{_safe_wiql_literal(created_by, 200)}'")
    if topic:
        conds.append(f"[System.Title] CONTAINS '{_safe_wiql_literal(topic, 200)}'")
    if area_path:
        conds.append(f"[System.AreaPath] UNDER '{_safe_wiql_literal(area_path, 300)}'")
    else:
        conds.append(
            "(" + " OR ".join(
                f"[System.AreaPath] UNDER '{_safe_wiql_literal(a, 300)}'" for a in DEVOPS_AREAS
            ) + ")"
        )
    result = await tool_query_workitems(" AND ".join(conds), top=sample_size)
    if "error" in result: return result
    ids = [it.get("id") for it in result.get("items",[]) if it.get("id")]
    samples = []
    if ids:
        det_fields = DEVOPS_FIELDS + ["System.Description","Microsoft.VSTS.Common.AcceptanceCriteria","System.Tags"]
        try:
            r = await devops_request_with_retry(
                "POST",
                _devops_url("wit/workitemsbatch?api-version=7.1"),
                _devops_headers(),
                {"ids": ids[:sample_size], "fields": det_fields},
                timeout=30,
            )
            if "error" not in r:
                for it in r.get("value",[]):
                    f=it.get("fields",{}); cb=f.get("System.CreatedBy",{})
                    samples.append({"id":it["id"],"title":f.get("System.Title","").replace(" | "," — "),"created_by":cb.get("displayName","") if isinstance(cb,dict) else str(cb),"description":(f.get("System.Description","") or "")[:2000],"acceptance_criteria":(f.get("Microsoft.VSTS.Common.AcceptanceCriteria","") or "")[:3000],"tags":f.get("System.Tags","")})
        except Exception as e:
            logging.error("[Tools] tool_analyze_patterns LLM block failed: %s", e)
    if not samples: samples = [{"id":it.get("id"),"title":it.get("title","")} for it in result.get("items",[])]
    return {"total_found": result.get("total_count",0), "samples_returned": len(samples), "analysis_data": samples}

async def tool_analyze_patterns_with_llm(created_by=None, topic=None, work_item_type="User Story", area_path=None, sample_size=15, analysis_type="template"):
    raw = await tool_analyze_patterns(created_by, topic, work_item_type, area_path, sample_size)
    if "error" in raw or raw.get("samples_returned",0)==0: return raw
    txt = ""
    for i,s in enumerate(raw.get("analysis_data",[])[:15],1):
        txt += f"\n--- Exemplo {i} (ID {s.get('id','?')}) ---\nTítulo: {s.get('title','')}\nCriado por: {s.get('created_by','')}\n"
        if s.get("description"): txt += f"Descrição: {s['description'][:600]}\n"
        if s.get("acceptance_criteria"): txt += f"Critérios: {s['acceptance_criteria'][:600]}\n"
    prompts = {"template": f"Analisa {raw['samples_returned']} {work_item_type}s e extrai PADRÃO DE ESCRITA.\n\n{txt}\n\nExtrai: 1.Estrutura 2.Linguagem 3.Campos 4.Template 5.Observações\nPT-PT.", "author_style": f"Analisa estilo de '{created_by or 'autor'}' em:\n\n{txt}\n\nDescreve: estilo, estrutura, vocabulário, detalhe, template.\nPT-PT."}
    fallback_prompt = f"Analisa:\n{txt}\nPT-PT."
    try: analysis = await llm_simple(f"És analista de padrões de escrita.\n\n{prompts.get(analysis_type, fallback_prompt)}", tier="standard", max_tokens=2000)
    except Exception as e:
        logging.error("[Tools] tool_analyze_patterns_with_llm failed: %s", e)
        analysis = f"Erro: {e}"
    profile_saved = False
    if analysis_type == "author_style" and created_by and isinstance(analysis, str) and not analysis.startswith("Erro:"):
        profile_saved = await _save_writer_profile(
            author_name=created_by,
            analysis=analysis,
            sample_ids=[s.get("id") for s in raw.get("analysis_data", []) if s.get("id")],
            sample_count=raw.get("samples_returned", 0),
            topic=topic or "",
            work_item_type=work_item_type,
        )

    return {
        "total_found": raw.get("total_found",0),
        "samples_analyzed": raw.get("samples_returned",0),
        "analysis_type": analysis_type,
        "analysis": analysis,
        "sample_ids": [s.get("id") for s in raw.get("analysis_data",[])],
        "writer_profile_saved": profile_saved,
    }

async def tool_generate_user_stories(topic, context="", num_stories=3, reference_area=None, reference_author=None, reference_topic=None):
    style_profile = None
    if reference_author:
        style_profile = await _load_writer_profile(reference_author)

    raw = {"samples_returned": 0, "analysis_data": []}
    reference_ids = []
    style_hint = ""
    ex = ""

    if style_profile and style_profile.get("style_analysis"):
        _log(f"generate_user_stories: using cached writer profile for '{reference_author}'")
        reference_ids = style_profile.get("sample_ids", [])
        style_hint = (
            f"\nPERFIL DE ESCRITA CACHEADO ({style_profile.get('author_name', reference_author)}):\n"
            f"{style_profile.get('style_analysis', '')[:3000]}\n"
        )
        ex = "(Perfil de autor carregado de WriterProfiles; não foi necessário reanalisar padrões.)"
    else:
        search_topic = reference_topic or topic
        raw = await tool_analyze_patterns(
            created_by=reference_author,
            topic=(search_topic[:35] if len(search_topic) > 35 else search_topic) or None,
            area_path=reference_area,
            sample_size=20,
        )
        if raw.get("samples_returned", 0) < 5:
            raw2 = await tool_analyze_patterns(
                created_by=reference_author,
                area_path=reference_area,
                sample_size=20,
            )
            if raw2.get("samples_returned", 0) > raw.get("samples_returned", 0):
                raw = raw2
        for i, s in enumerate(raw.get("analysis_data", [])[:8], 1):
            ex += f"\n{'='*50}\nEXEMPLO {i} (ID:{s.get('id','?')})\n{'='*50}\nTÍTULO: {s.get('title','')}\nCRIADOR: {s.get('created_by','')}\n"
            if s.get("description"):
                clean_desc = _clean_html_for_example(s["description"][:1500])
                ex += f"DESC:\n{clean_desc}\n"
            if s.get("acceptance_criteria"):
                clean_ac = _clean_html_for_example(s["acceptance_criteria"][:2000])
                ex += f"AC:\n{clean_ac}\n"
        if not ex:
            ex = "(Sem exemplos — usa boas práticas)"
        reference_ids = [s.get("id") for s in raw.get("analysis_data", [])]

    prompt = f"""TAREFA: Gerar {num_stories} User Story(ies) sobre: "{topic}"

{'='*60}
EXEMPLOS REAIS DA EQUIPA (aprende granularidade e estrutura):
{'='*60}
{ex}

{style_hint}

{'='*60}
CONTEXTO ADICIONAL DO PEDIDO:
{'='*60}
{context or "Nenhum contexto adicional fornecido."}

{'='*60}
INSTRUÇÕES DE OUTPUT:
{'='*60}
Para CADA User Story, gera EXACTAMENTE neste formato:

### User Story {{N}}

**Título**: MSE | [Área] | [Sub-área] | [Funcionalidade] | [Detalhe]

**Descrição**:
<div>
Eu como <b>[Persona]</b>, quero <b>[ação]</b>, para que <b>[benefício]</b>.
</div>

**Critérios de Aceitação**:
<b>Objetivo / Âmbito</b>
<ul>
<li>...</li>
</ul>

<b>Composição Visual / Layout</b>
<ul>
<li>...</li>
</ul>

<b>Comportamento / Regras de Negócio</b>
<ul>
<li>...</li>
</ul>

<b>Mockup / Referência Visual</b>
<ul>
<li>...</li>
</ul>

LEMBRA-TE:
- Segue o padrão exacto dos exemplos acima (granularidade, nível de detalhe, vocabulário)
- HTML limpo APENAS (<b>, <ul>, <li>, <br>, <div>)
- PT-PT, testável, auto-contida
- Vocabulário MSE: CTA, Enable/Disable, Input, Dropdown, Stepper, Toast, Modal, FEE"""
    sys_msg = """Tu és Product Owner Sénior no Millennium Site Empresas (MSE), especialista em User Stories de alta qualidade.

PAPEL: Geras User Stories que seguem rigorosamente o padrão MSE. A tua qualidade define o standard da equipa.

FORMATO OBRIGATÓRIO DE TÍTULO:
MSE | [Área] | [Sub-área] | [Funcionalidade] | [Detalhe Específico]
Exemplo: MSE | Pagamentos | SEPA | Transferência Nacional | Validação IBAN

FORMATO OBRIGATÓRIO DE DESCRIÇÃO (HTML limpo):
<div>
Eu como <b>[Persona — ex: Utilizador Empresa, Gestor de Conta, Administrador]</b>,
quero <b>[ação concreta e específica]</b>,
para que <b>[benefício claro e mensurável]</b>.
</div>

FORMATO OBRIGATÓRIO DE ACCEPTANCE CRITERIA (HTML limpo):
Divididos em secções com <b>bold</b> headers:

<b>Objetivo / Âmbito</b>
<ul>
<li>Descrever o que está em scope e o que NÃO está</li>
<li>Contexto da funcionalidade dentro do MSE</li>
</ul>

<b>Composição Visual / Layout</b>
<ul>
<li>Elementos UI: CTAs, inputs, dropdowns, select boxes, steppers, toggles</li>
<li>Estados: enable/disable, loading, empty state, error state</li>
<li>Labels, placeholders, tooltips — textos exactos quando possível</li>
</ul>

<b>Comportamento / Regras de Negócio</b>
<ul>
<li>Validações de campo (formato, obrigatoriedade, limites)</li>
<li>Lógica condicional (se X então Y)</li>
<li>Mensagens de erro específicas</li>
<li>Toasts de sucesso/erro</li>
<li>Comportamento de modais (abrir, confirmar, cancelar)</li>
</ul>

<b>Mockup / Referência Visual</b>
<ul>
<li>Referência ao mockup se fornecido (ex: "Conforme imagem 1 — ecrã de listagem")</li>
<li>Se não houver mockup: descrever layout esperado</li>
</ul>

VOCABULÁRIO MSE OBRIGATÓRIO:
- CTA (Call To Action), Enable/Disable, Input, Dropdown/Select box
- Stepper, Toast (sucesso/erro/warning), Modal, Header, Sidebar
- FEE (Front End Empresas), Backoffice, API, Endpoint
- Validação inline, Placeholder, Label, Tooltip, Loading spinner
- Estado: Activo/Inactivo, Visível/Oculto, Editável/Só-leitura

REGRAS ABSOLUTAS:
1. HTML LIMPO apenas: <b>, <ul>, <li>, <br>, <div>. NUNCA <font>, <span style>, &nbsp; ou qualquer HTML sujo.
2. PT-PT sempre. Sem anglicismos desnecessários (usar "Utilizador" não "User", mas manter termos técnicos: CTA, Toast, Modal).
3. Cada AC deve ser TESTÁVEL — um QA deve conseguir validar com YES/NO.
4. Granularidade: uma US = uma funcionalidade atómica. Se for muito grande, dividir.
5. Auto-contida: a US deve fazer sentido sem ler outras USs.
6. Sem contradições internas.
7. APRENDE a granularidade dos exemplos fornecidos — não inventes nível de detalhe diferente do que a equipa usa."""
    try: gen = await llm_simple(f"{sys_msg}\n\n{prompt}", tier="standard", max_tokens=8000)
    except Exception as e:
        logging.error("[Tools] tool_generate_user_stories failed: %s", e)
        gen = f"Erro: {e}"
    return {
        "generated_user_stories": gen,
        "based_on_examples": raw.get("samples_returned", 0) if raw else 0,
        "reference_ids": reference_ids,
        "used_writer_profile": bool(style_profile),
        "topic": topic,
        "num_requested": num_stories,
    }

async def tool_query_hierarchy(
    parent_id=None,
    parent_type="Epic",
    child_type="User Story",
    area_path=None,
    title_contains=None,
    parent_title_hint=None,
):
    try:
        safe_parent_type = _validate_workitem_type(parent_type, "Epic")
        safe_child_type = _validate_workitem_type(child_type, "User Story")
    except ValueError as e:
        return {"error": str(e)}

    canonical_area = _canonicalize_area_path(area_path) if area_path else ""
    safe_area = _safe_wiql_literal(canonical_area, 300) if canonical_area else ""
    parent_hint = str(parent_title_hint or "").strip()
    child_title_filter = str(title_contains or "").strip()

    headers = _devops_headers()
    resolved_meta = {"attempted": False}
    safe_parent_id = None
    if parent_id:
        try:
            safe_parent_id = int(parent_id)
        except (TypeError, ValueError):
            return {"error": "parent_id inválido: deve ser inteiro positivo"}
        if safe_parent_id <= 0:
            return {"error": "parent_id inválido: deve ser inteiro positivo"}
    elif parent_hint:
        resolved_parent_id, resolved_meta = await _resolve_parent_id_by_title_hint(
            headers,
            parent_type=safe_parent_type,
            area_path=safe_area,
            title_hint=parent_hint,
        )
        if not resolved_parent_id and safe_area:
            fallback_id, fallback_meta = await _resolve_parent_id_by_title_hint(
                headers,
                parent_type=safe_parent_type,
                area_path="",
                title_hint=parent_hint,
            )
            resolved_meta["fallback_without_area_attempted"] = True
            resolved_meta["fallback_without_area_meta"] = fallback_meta
            if fallback_id:
                resolved_parent_id = fallback_id
                resolved_meta["fallback_without_area_used"] = True
        if resolved_parent_id:
            safe_parent_id = int(resolved_parent_id)
            # Neste caminho, o hint foi usado para resolver o PAI e não para filtrar o TÍTULO dos filhos.
            child_title_filter = ""
        else:
            return {
                "error": (
                    f"Não foi possível identificar {safe_parent_type} com título '{parent_hint}'. "
                    "Indica o ID do parent para resultado exato."
                ),
                "total_count": 0,
                "items_returned": 0,
                "items": [],
                "parent_id": parent_id,
                "parent_type": safe_parent_type,
                "child_type": safe_child_type,
                "title_contains": child_title_filter,
                "parent_title_hint": parent_hint,
                "_parent_resolve": resolved_meta,
            }

    if safe_parent_id:
        af = f"AND ([Target].[System.AreaPath] UNDER '{safe_area}')" if safe_area else ""
        wiql = (
            "SELECT [System.Id] FROM WorkItemLinks WHERE "
            f"([Source].[System.Id] = {safe_parent_id}) "
            "AND ([System.Links.LinkType] = 'System.LinkTypes.Hierarchy-Forward') "
            f"AND ([Target].[System.WorkItemType] = '{_safe_wiql_literal(safe_child_type, 80)}') "
            f"AND ([Target].[System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}') "
            f"{af} MODE (Recursive)"
        )
    else:
        source_af = f"AND [Source].[System.AreaPath] UNDER '{safe_area}'" if safe_area else ""
        target_af = f"AND [Target].[System.AreaPath] UNDER '{safe_area}'" if safe_area else ""
        wiql = (
            "SELECT [System.Id] FROM WorkItemLinks WHERE "
            f"([Source].[System.WorkItemType] = '{_safe_wiql_literal(safe_parent_type, 80)}' "
            f"{source_af} AND [Source].[System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}') "
            "AND ([System.Links.LinkType] = 'System.LinkTypes.Hierarchy-Forward') "
            f"AND ([Target].[System.WorkItemType] = '{_safe_wiql_literal(safe_child_type, 80)}') "
            f"AND ([Target].[System.TeamProject] = '{_safe_wiql_literal(DEVOPS_PROJECT, 120)}') "
            f"{target_af} "
            "MODE (Recursive)"
        )

    resp = await devops_request_with_retry(
        "POST",
        _devops_url("wit/wiql?api-version=7.1"),
        headers,
        {"query": wiql},
        timeout=60,
    )
    if "error" in resp:
        return resp
    rels = resp.get("workItemRelations", [])
    tids = list(set(r["target"]["id"] for r in rels if r.get("target") and r.get("rel")))
    if not tids:
        tids = [wi["id"] for wi in resp.get("workItems", [])]
    total_raw = len(tids)
    if not tids:
        return {
            "total_count": 0,
            "total_raw_count": 0,
            "items_returned": 0,
            "items": [],
            "parent_id": safe_parent_id if safe_parent_id else parent_id,
            "parent_type": safe_parent_type,
            "child_type": safe_child_type,
            "title_contains": child_title_filter,
            "parent_title_hint": parent_hint,
        }
    flds = DEVOPS_FIELDS + ["System.Parent"]
    all_det, failed = [], []
    for i in range(0, len(tids), 100):
        batch = tids[i : i + 100]
        r = await devops_request_with_retry(
            "POST",
            _devops_url("wit/workitemsbatch?api-version=7.1"),
            headers,
            {"ids": batch, "fields": flds},
            timeout=60,
        )
        if "error" not in r:
            all_det.extend(r.get("value", []))
        else:
            failed.extend(batch)
        await asyncio.sleep(0.5)
    items = []
    for it in all_det:
        fi = _format_wi(it)
        fi["parent_id"] = it.get("fields", {}).get("System.Parent")
        items.append(fi)
    # Filtro defensivo final: garante tipo e área pedidos, mesmo se WIQL trouxer ruído.
    filtered_out = 0
    if safe_child_type or safe_area:
        expected_type = str(safe_child_type or "").strip().lower()
        expected_area = str(safe_area or "").strip().lower()
        filtered = []
        for item in items:
            item_type = str(item.get("type", "") or "").strip().lower()
            item_area = str(item.get("area", "") or "").strip().lower()
            type_ok = not expected_type or item_type == expected_type
            area_ok = not expected_area or item_area.startswith(expected_area)
            if type_ok and area_ok:
                filtered.append(item)
            else:
                filtered_out += 1
        items = filtered
    title_filter = _normalize_match_text(child_title_filter)
    if title_filter:
        terms = [t for t in title_filter.split(" ") if t]
        if terms:
            by_title = []
            for item in items:
                title_norm = _normalize_match_text(str(item.get("title", "") or ""))
                if all(term in title_norm for term in terms):
                    by_title.append(item)
                else:
                    filtered_out += 1
            items = by_title

    if failed and not items:
        items = [{"id":fid,"type":child_type,"title":"(rate limited)","state":"","url":f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{fid}"} for fid in failed]
    matched_count = len(items)
    result = {
        "total_count": matched_count,
        "total_raw_count": total_raw,
        "items_returned": matched_count,
        "parent_id": safe_parent_id if safe_parent_id else parent_id,
        "parent_type":safe_parent_type,
        "child_type":safe_child_type,
        "title_contains": child_title_filter,
        "parent_title_hint": parent_hint,
        "items":items,
    }
    await _attach_auto_csv_export(
        result,
        title_hint=f"hierarchy_{safe_parent_type}_{safe_child_type}_{(safe_parent_id if safe_parent_id else 'all')}",
    )
    if resolved_meta.get("attempted"):
        result["_parent_resolve"] = resolved_meta
    if filtered_out:
        result["_post_filtered_out"] = filtered_out
    if failed:
        result["_partial"] = True
        result["_failed_batch_count"] = len(failed)
    return result

async def tool_compute_kpi(wiql_where, group_by=None, kpi_type="count"):
    result = await tool_query_workitems(wiql_where=wiql_where, top=1000)
    if "error" in result: return result
    items = result.get("items",[]); total = result.get("total_count",len(items))
    kpi = {"total_count": total, "items_analyzed": len(items)}
    if group_by and items:
        fm = {"state":"state","estado":"state","type":"type","tipo":"type","assigned_to":"assigned_to","assignee":"assigned_to","created_by":"created_by","criador":"created_by","autor":"created_by","area":"area","area_path":"area"}
        fk = fm.get(group_by.lower(), group_by.lower())
        grps = {}
        for it in items: v=it.get(fk,"N/A") or "N/A"; grps[v]=grps.get(v,0)+1
        kpi["group_by"]=group_by; kpi["groups"]=[{"value":k,"count":v} for k,v in sorted(grps.items(),key=lambda x:x[1],reverse=True)]; kpi["unique_values"]=len(grps)
    if kpi_type=="timeline" and items:
        m={}
        for it in items:
            d=it.get("created_date","")
            if d: mo=d[:7]; m[mo]=m.get(mo,0)+1
        kpi["timeline"]=sorted(m.items())
    if kpi_type=="distribution" and items:
        st,tp = {},{}
        for it in items: s=it.get("state","?"); st[s]=st.get(s,0)+1; t=it.get("type","?"); tp[t]=tp.get(t,0)+1
        kpi["state_distribution"]=st; kpi["type_distribution"]=tp
    return kpi

async def tool_create_workitem(
    work_item_type: str = "User Story",
    title: str = "",
    description: str = "",
    acceptance_criteria: str = "",
    area_path: str = "",
    assigned_to: str = "",
    tags: str = "",
    confirmed: bool = False,
):
    """Cria um Work Item no Azure DevOps via JSON Patch."""
    normalized_type = (work_item_type or "User Story").strip().lower()
    allowed_types = {
        "user story": "User Story",
        "bug": "Bug",
        "task": "Task",
        "feature": "Feature",
    }
    work_item_type = allowed_types.get(normalized_type, "User Story")

    title = (title or "").strip()[:250]
    description = (description or "").strip()[:12000]
    acceptance_criteria = (acceptance_criteria or "").strip()[:12000]
    area_path = (area_path or "").strip()[:300]
    assigned_to = (assigned_to or "").strip()[:200]
    tags = (tags or "").strip()[:500]

    if not confirmed:
        return {"error": "Confirmação explícita necessária (envia confirmed=true após 'confirmo')."}
    if not title:
        return {"error": "Título é obrigatório"}

    _log(f"create_workitem: type={work_item_type}, title={title[:60]}...")

    patch_doc = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
    ]
    if description:
        patch_doc.append({"op": "add", "path": "/fields/System.Description", "value": description})
    if acceptance_criteria:
        patch_doc.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "value": acceptance_criteria})
    if area_path:
        patch_doc.append({"op": "add", "path": "/fields/System.AreaPath", "value": area_path})
    if assigned_to:
        patch_doc.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to})
    if tags:
        patch_doc.append({"op": "add", "path": "/fields/System.Tags", "value": tags})

    wi_type_encoded = quote(work_item_type, safe="")
    url = _devops_url(f"wit/workitems/${wi_type_encoded}?api-version=7.1")
    headers = _devops_headers()
    headers["Content-Type"] = "application/json-patch+json"
    data = await devops_request_with_retry(
        "POST",
        url,
        headers,
        content_body=json.dumps(patch_doc),
        max_retries=3,
        timeout=30,
    )
    if "error" in data:
        return data

    wi_id = data.get("id")
    wi_url = data.get("_links", {}).get("html", {}).get("href", "")
    if not wi_url and wi_id:
        wi_url = f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{wi_id}"

    return {
        "created": True,
        "id": wi_id,
        "url": wi_url,
        "title": title,
        "work_item_type": work_item_type,
        "area_path": area_path or "(default)",
    }

async def tool_refine_workitem(
    work_item_id: int = 0,
    refinement_request: str = "",
):
    """Refina uma US existente com base numa instrução curta, sem alterar DevOps."""
    try:
        safe_id = int(work_item_id)
    except (TypeError, ValueError):
        return {"error": "work_item_id inválido: deve ser inteiro positivo"}
    if safe_id <= 0:
        return {"error": "work_item_id inválido: deve ser inteiro positivo"}

    req = (refinement_request or "").strip()
    if not req:
        return {"error": "refinement_request é obrigatório"}

    fields = [
        "System.Id",
        "System.Title",
        "System.State",
        "System.WorkItemType",
        "System.AreaPath",
        "System.Description",
        "Microsoft.VSTS.Common.AcceptanceCriteria",
        "System.Tags",
    ]
    fields_param = ",".join(fields)
    headers = _devops_headers()

    wi = await devops_request_with_retry(
        "GET",
        _devops_url(f"wit/workitems/{safe_id}?fields={fields_param}&api-version=7.1"),
        headers,
        max_retries=3,
        timeout=45,
    )
    if "error" in wi:
        return wi
    if not isinstance(wi, dict) or not wi.get("id"):
        return {"error": "Work item não encontrado"}

    f = wi.get("fields", {})
    original = {
        "id": wi.get("id"),
        "title": f.get("System.Title", ""),
        "state": f.get("System.State", ""),
        "type": f.get("System.WorkItemType", ""),
        "area": f.get("System.AreaPath", ""),
        "description_html": f.get("System.Description", "") or "",
        "acceptance_criteria_html": f.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or "",
        "tags": f.get("System.Tags", "") or "",
        "url": f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{safe_id}",
    }

    prompt = f"""És PO Sénior MSE.
Recebeste uma User Story existente e um pedido de refinamento.

US ORIGINAL:
- ID: {original['id']}
- Tipo: {original['type']}
- Título: {original['title']}
- Área: {original['area']}
- Descrição HTML: {original['description_html'][:6000]}
- AC HTML: {original['acceptance_criteria_html'][:6000]}
- Tags: {original['tags']}

PEDIDO DE REFINAMENTO:
{req}

Objetivo:
- Devolver uma versão revista, mantendo estilo MSE e estrutura testável.
- Aplicar apenas as mudanças pedidas.
- PT-PT.
- HTML limpo (div, b, ul, li, br).

Responde APENAS em JSON válido neste formato:
{{
  "title": "Título revisto",
  "description_html": "<div>...</div>",
  "acceptance_criteria_html": "<ul><li>...</li></ul>",
  "change_summary": "Resumo curto das alterações"
}}"""

    try:
        llm_output = await llm_simple(prompt, tier="standard", max_tokens=2600)
    except Exception as e:
        return {"error": f"Falha LLM ao refinar work item: {str(e)}"}

    parsed = _extract_json_object(llm_output or "")
    if not parsed:
        return {
            "work_item_id": safe_id,
            "work_item_url": original["url"],
            "refinement_request": req,
            "original": original,
            "ready_to_apply": False,
            "error": "Não foi possível estruturar JSON da revisão. Repetir pedido com instrução mais objetiva.",
            "refined_raw": (llm_output or "")[:12000],
            "note": "Esta tool não altera o work item no DevOps; gera apenas proposta de revisão.",
        }

    refined = {
        "title": str(parsed.get("title", "")).strip() or original["title"],
        "description_html": str(parsed.get("description_html", "")).strip(),
        "acceptance_criteria_html": str(parsed.get("acceptance_criteria_html", "")).strip(),
        "change_summary": str(parsed.get("change_summary", "")).strip(),
    }

    return {
        "work_item_id": safe_id,
        "work_item_url": original["url"],
        "refinement_request": req,
        "original": original,
        "refined": refined,
        "ready_to_apply": True,
        "note": "Esta tool não altera o work item no DevOps; gera proposta para revisão DRAFT->REVIEW->FINAL.",
    }
