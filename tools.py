# =============================================================================
# tools.py — Tool definitions, implementations e system prompts v7.0
# =============================================================================

import json, base64, asyncio, logging
from datetime import datetime
from collections import deque
from urllib.parse import quote
import httpx

from config import (
    DEVOPS_PAT, DEVOPS_ORG, DEVOPS_PROJECT,
    SEARCH_SERVICE, SEARCH_KEY, API_VERSION_SEARCH,
    DEVOPS_INDEX, OMNI_INDEX,
    DEVOPS_FIELDS, DEVOPS_AREAS,
    AGENT_TOOL_RESULT_MAX_SIZE, AGENT_TOOL_RESULT_KEEP_ITEMS, DEBUG_LOG_SIZE,
)
from llm_provider import get_embedding_provider, llm_simple

_devops_debug_log: deque = deque(maxlen=DEBUG_LOG_SIZE)
def get_devops_debug_log(): return list(_devops_debug_log)
def _log(msg):
    _devops_debug_log.append({"ts": datetime.now().isoformat(), "msg": msg})
    logging.info("[Tools] %s", msg)

# --- DevOps helpers ---
async def _devops_request_with_retry(client, method, url, headers, json_body=None, max_retries=5):
    last_status = None
    for attempt in range(max_retries):
        try:
            resp = await (client.post(url, json=json_body, headers=headers) if method == "POST" else client.get(url, headers=headers))
            last_status = resp.status_code
            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", 3*(attempt+1))), 30)
                _log(f"429, attempt {attempt+1}/{max_retries}, wait {wait}s")
                await asyncio.sleep(wait); continue
            if resp.status_code >= 500:
                await asyncio.sleep(2*(attempt+1)); continue
            if resp.status_code >= 400:
                _log(f"{resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if attempt == max_retries-1: return {"error": f"DevOps {e.response.status_code}: {e.response.text[:200]}"}
            await asyncio.sleep(1)
        except httpx.TimeoutException:
            if attempt == max_retries-1: return {"error": f"DevOps timeout após {max_retries} tentativas"}
            await asyncio.sleep(2*(attempt+1))
        except Exception as e:
            if attempt == max_retries-1: return {"error": f"DevOps erro: {str(e)}"}
    return {"error": f"Max retries (last status: {last_status})"}


async def _search_request_with_retry(url, headers, json_body, max_retries=3):
    """POST ao Azure AI Search com retries para 429/5xx/timeouts."""
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.post(url, json=json_body, headers=headers)

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        wait = int(float(retry_after)) if retry_after is not None else 2 ** (attempt - 1)
                    except (TypeError, ValueError):
                        wait = 2 ** (attempt - 1)
                    wait = max(1, min(wait, 30))
                    if attempt == max_retries:
                        logging.warning(
                            "[Search] 429 attempt %s/%s, sem retries restantes",
                            attempt, max_retries,
                        )
                        return {"error": f"Search 429 após {max_retries} tentativas"}
                    logging.warning(
                        "[Search] 429 attempt %s/%s, retry em %ss",
                        attempt, max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    wait = min(2 ** (attempt - 1), 30)
                    if attempt == max_retries:
                        logging.warning(
                            "[Search] %s attempt %s/%s, sem retries restantes",
                            resp.status_code, attempt, max_retries,
                        )
                        return {"error": f"Search {resp.status_code} após {max_retries} tentativas"}
                    logging.warning(
                        "[Search] %s attempt %s/%s, retry em %ss",
                        resp.status_code, attempt, max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    return {"error": f"Search {resp.status_code}: {resp.text[:200]}"}

                return resp.json()

            except httpx.TimeoutException:
                wait = min(2 ** (attempt - 1), 30)
                if attempt == max_retries:
                    logging.warning(
                        "[Search] timeout attempt %s/%s, sem retries restantes",
                        attempt, max_retries,
                    )
                    return {"error": f"Search timeout após {max_retries} tentativas"}
                logging.warning(
                    "[Search] timeout attempt %s/%s, retry em %ss",
                    attempt, max_retries, wait,
                )
                await asyncio.sleep(wait)
            except httpx.RequestError as e:
                wait = min(2 ** (attempt - 1), 30)
                if attempt == max_retries:
                    logging.warning(
                        "[Search] request error attempt %s/%s (%s), sem retries restantes",
                        attempt, max_retries, str(e),
                    )
                    return {"error": f"Search request error após {max_retries} tentativas: {str(e)}"}
                logging.warning(
                    "[Search] request error attempt %s/%s (%s), retry em %ss",
                    attempt, max_retries, str(e), wait,
                )
                await asyncio.sleep(wait)
            except Exception as e:
                wait = min(2 ** (attempt - 1), 30)
                if attempt == max_retries:
                    logging.warning(
                        "[Search] erro inesperado attempt %s/%s (%s), sem retries restantes",
                        attempt, max_retries, str(e),
                    )
                    return {"error": f"Search erro: {str(e)}"}
                logging.warning(
                    "[Search] erro inesperado attempt %s/%s (%s), retry em %ss",
                    attempt, max_retries, str(e), wait,
                )
                await asyncio.sleep(wait)

    return {"error": "Search erro desconhecido"}

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

async def get_embedding(text):
    try:
        return await get_embedding_provider().embed(text[:8000].strip() or " ")
    except Exception as e:
        logging.error("[Tools] get_embedding failed: %s", e)
        return None

# =============================================================================
# TOOL 1: query_workitems
# =============================================================================
async def tool_query_workitems(wiql_where, fields=None, top=200):
    _log(f"query_workitems: top={top}, wiql={wiql_where[:80]}...")
    use_fields = fields if fields and len(fields) > 0 else DEVOPS_FIELDS
    wiql = f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '{DEVOPS_PROJECT}' AND {wiql_where} ORDER BY [System.ChangedDate] DESC"
    headers = _devops_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await _devops_request_with_retry(client, "POST", _devops_url("wit/wiql?api-version=7.1"), headers, {"query": wiql})
        if "error" in resp: return resp
        work_items = resp.get("workItems", [])
        total_count = len(work_items)
        if top == 0: return {"total_count": total_count, "items": []}
        work_items = work_items[:min(top, 1000) if top > 0 else total_count]
        if not work_items: return {"total_count": 0, "items": []}
        await asyncio.sleep(0.5)
        all_details, failed_ids, ids = [], [], [wi["id"] for wi in work_items]
        for i in range(0, len(ids), 100):
            batch = ids[i:i+100]
            r = await _devops_request_with_retry(client, "POST", _devops_url("wit/workitemsbatch?api-version=7.1"), headers, {"ids": batch, "fields": use_fields})
            if "error" in r: failed_ids.extend(batch); await asyncio.sleep(3); continue
            all_details.extend(r.get("value",[])); await asyncio.sleep(0.5)
        if failed_ids and len(failed_ids) <= 50:
            await asyncio.sleep(2)
            fl = ",".join(use_fields)
            for fid in failed_ids[:]:
                r = await _devops_request_with_retry(client, "GET", _devops_url(f"wit/workitems/{fid}?fields={fl}&api-version=7.1"), headers, max_retries=3)
                if "error" not in r and "id" in r: all_details.append(r); failed_ids.remove(fid)
                await asyncio.sleep(0.3)
        items = [_format_wi(it) for it in all_details]
        if failed_ids and not items:
            items = [{"id":fid,"type":"","title":"(rate limited)","state":"","url":f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{fid}"} for fid in failed_ids]
        result = {"total_count": total_count, "items_returned": len(items), "items": items}
        if failed_ids: result["_partial"] = True; result["_failed_batch_count"] = len(failed_ids)
        return result

# =============================================================================
# TOOL 2: search_workitems
# =============================================================================
async def tool_search_workitems(query, top=30, filter_expr=None):
    emb = await get_embedding(query)
    if not emb: return {"error": "Falha embedding"}
    body = {"vectorQueries":[{"kind":"vector","vector":emb,"fields":"content_vector","k":top}],"select":"id,content,url,tag,status","top":top}
    if filter_expr: body["filter"] = filter_expr
    url = f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{DEVOPS_INDEX}/docs/search?api-version={API_VERSION_SEARCH}"
    data = await _search_request_with_retry(
        url=url,
        headers={"api-key": SEARCH_KEY, "Content-Type": "application/json"},
        json_body=body,
        max_retries=3,
    )
    if "error" in data:
        return {"error": data["error"]}
    items = []
    for d in data.get("value",[]):
        ct = d.get("content","")
        items.append({"id":d.get("id",""),"title":ct.split("]")[0].replace("[","") if "]" in ct else ct[:100],"content":ct[:500],"status":d.get("status",""),"url":d.get("url",""),"score":round(d.get("@search.score",0),4)})
    return {"total_results": len(items), "items": items}

# =============================================================================
# TOOL 3: search_website
# =============================================================================
async def tool_search_website(query, top=10):
    emb = await get_embedding(query)
    if not emb: return {"error": "Falha embedding"}
    body = {"vectorQueries":[{"kind":"vector","vector":emb,"fields":"content_vector","k":top}],"select":"id,content,url,tag","top":top}
    url = f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{OMNI_INDEX}/docs/search?api-version={API_VERSION_SEARCH}"
    data = await _search_request_with_retry(
        url=url,
        headers={"api-key": SEARCH_KEY, "Content-Type": "application/json"},
        json_body=body,
        max_retries=3,
    )
    if "error" in data:
        return {"error": data["error"]}
    return {"total_results": len(data.get("value",[])), "items": [{"id":d.get("id",""),"content":d.get("content","")[:500],"url":d.get("url",""),"tag":d.get("tag",""),"score":round(d.get("@search.score",0),4)} for d in data.get("value",[])]}

# =============================================================================
# TOOL 4: analyze_patterns
# =============================================================================
async def tool_analyze_patterns(created_by=None, topic=None, work_item_type="User Story", area_path=None, sample_size=15):
    conds = [f"[System.WorkItemType]='{work_item_type}'"]
    if created_by: conds.append(f"[System.CreatedBy] CONTAINS '{created_by}'")
    if topic: conds.append(f"[System.Title] CONTAINS '{topic}'")
    if area_path: conds.append(f"[System.AreaPath] UNDER '{area_path}'")
    else: conds.append("(" + " OR ".join(f"[System.AreaPath] UNDER '{a}'" for a in DEVOPS_AREAS) + ")")
    result = await tool_query_workitems(" AND ".join(conds), top=sample_size)
    if "error" in result: return result
    ids = [it.get("id") for it in result.get("items",[]) if it.get("id")]
    samples = []
    if ids:
        det_fields = DEVOPS_FIELDS + ["System.Description","Microsoft.VSTS.Common.AcceptanceCriteria","System.Tags"]
        async with httpx.AsyncClient(timeout=30) as c:
            try:
                r = await _devops_request_with_retry(c, "POST", _devops_url("wit/workitemsbatch?api-version=7.1"), _devops_headers(), {"ids":ids[:sample_size],"fields":det_fields})
                if "error" not in r:
                    for it in r.get("value",[]):
                        f=it.get("fields",{}); cb=f.get("System.CreatedBy",{})
                        samples.append({"id":it["id"],"title":f.get("System.Title","").replace(" | "," — "),"created_by":cb.get("displayName","") if isinstance(cb,dict) else str(cb),"description":(f.get("System.Description","") or "")[:1000],"acceptance_criteria":(f.get("Microsoft.VSTS.Common.AcceptanceCriteria","") or "")[:1000],"tags":f.get("System.Tags","")})
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
    try: analysis = await llm_simple(f"És analista de padrões de escrita.\n\n{prompts.get(analysis_type, f'Analisa:\n{txt}\nPT-PT.')}", tier="standard", max_tokens=2000)
    except Exception as e:
        logging.error("[Tools] tool_analyze_patterns_with_llm failed: %s", e)
        analysis = f"Erro: {e}"
    return {"total_found": raw.get("total_found",0), "samples_analyzed": raw.get("samples_returned",0), "analysis_type": analysis_type, "analysis": analysis, "sample_ids": [s.get("id") for s in raw.get("analysis_data",[])]}

# =============================================================================
# TOOL 5: generate_user_stories
# =============================================================================
async def tool_generate_user_stories(topic, context="", num_stories=3, reference_area=None, reference_author=None, reference_topic=None):
    search_topic = reference_topic or topic
    raw = await tool_analyze_patterns(created_by=reference_author, topic=(search_topic[:35] if len(search_topic)>35 else search_topic) or None, area_path=reference_area, sample_size=20)
    if raw.get("samples_returned",0) < 5:
        raw2 = await tool_analyze_patterns(created_by=reference_author, area_path=reference_area, sample_size=20)
        if raw2.get("samples_returned",0) > raw.get("samples_returned",0): raw = raw2
    ex = ""
    for i,s in enumerate(raw.get("analysis_data",[])[:12],1):
        ex += f"\n{'='*50}\nEXEMPLO {i} (ID:{s.get('id','?')})\n{'='*50}\nTÍTULO: {s.get('title','')}\nCRIADOR: {s.get('created_by','')}\n"
        if s.get("description"): ex += f"DESC:\n{s['description'][:800]}\n"
        if s.get("acceptance_criteria"): ex += f"AC:\n{s['acceptance_criteria'][:800]}\n"
    if not ex: ex = "(Sem exemplos — usa boas práticas)"
    prompt = f'Gerar {num_stories} USs sobre "{topic}".\n\nEXEMPLOS REAIS:\n{ex}\n\nCONTEXTO: {context or "Nenhum."}\n\nINSTRUÇÕES: Mesmo padrão, HTML limpo, vocabulário MSE, Título: MSE|Área|Sub|Func|Detalhe.\nPT-PT.'
    sys_msg = "REGRA: Aprende granularidade dos exemplos, NÃO copies HTML sujo. Tu és PO Sénior MSE."
    try: gen = await llm_simple(f"{sys_msg}\n\n{prompt}", tier="standard", max_tokens=8000)
    except Exception as e:
        logging.error("[Tools] tool_generate_user_stories failed: %s", e)
        gen = f"Erro: {e}"
    return {"generated_user_stories": gen, "based_on_examples": raw.get("samples_returned",0), "reference_ids": [s.get("id") for s in raw.get("analysis_data",[])], "topic": topic, "num_requested": num_stories}

# =============================================================================
# TOOL 6: query_hierarchy
# =============================================================================
async def tool_query_hierarchy(parent_id=None, parent_type="Epic", child_type="User Story", area_path=None):
    if parent_id:
        af = f"AND ([Target].[System.AreaPath] UNDER '{area_path}')" if area_path else ""
        wiql = f"SELECT [System.Id] FROM WorkItemLinks WHERE ([Source].[System.Id] = {parent_id}) AND ([System.Links.LinkType] = 'System.LinkTypes.Hierarchy-Forward') AND ([Target].[System.WorkItemType] = '{child_type}') AND ([Target].[System.TeamProject] = '{DEVOPS_PROJECT}') {af} MODE (Recursive)"
    else:
        af = f"AND [Source].[System.AreaPath] UNDER '{area_path}'" if area_path else ""
        wiql = f"SELECT [System.Id] FROM WorkItemLinks WHERE ([Source].[System.WorkItemType] = '{parent_type}' {af} AND [Source].[System.TeamProject] = '{DEVOPS_PROJECT}') AND ([System.Links.LinkType] = 'System.LinkTypes.Hierarchy-Forward') AND ([Target].[System.WorkItemType] = '{child_type}') MODE (Recursive)"
    headers = _devops_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await _devops_request_with_retry(client, "POST", _devops_url("wit/wiql?api-version=7.1"), headers, {"query": wiql})
        if "error" in resp: return resp
        rels = resp.get("workItemRelations",[])
        tids = list(set(r["target"]["id"] for r in rels if r.get("target") and r.get("rel")))
        if not tids: tids = [wi["id"] for wi in resp.get("workItems",[])]
        total = len(tids)
        if not tids: return {"total_count":0,"items":[],"parent_id":parent_id,"parent_type":parent_type}
        flds = DEVOPS_FIELDS + ["System.Parent"]
        all_det, failed = [], []
        for i in range(0,len(tids),100):
            batch = tids[i:i+100]
            r = await _devops_request_with_retry(client,"POST",_devops_url("wit/workitemsbatch?api-version=7.1"),headers,{"ids":batch,"fields":flds})
            if "error" not in r: all_det.extend(r.get("value",[])) 
            else: failed.extend(batch)
            await asyncio.sleep(0.5)
        items = []
        for it in all_det:
            fi = _format_wi(it); fi["parent_id"] = it.get("fields",{}).get("System.Parent"); items.append(fi)
        if failed and not items:
            items = [{"id":fid,"type":child_type,"title":"(rate limited)","state":"","url":f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{fid}"} for fid in failed]
        result = {"total_count":total,"items_returned":len(items),"parent_id":parent_id,"parent_type":parent_type,"child_type":child_type,"items":items}
        if failed: result["_partial"]=True; result["_failed_batch_count"]=len(failed)
        return result

# =============================================================================
# TOOL 7: compute_kpi
# =============================================================================
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

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    url,
                    headers=headers,
                    content=json.dumps(patch_doc),
                )
                if resp.status_code == 429:
                    wait = min(int(resp.headers.get("Retry-After", 3 * (attempt + 1))), 30)
                    _log(f"create_workitem 429, attempt {attempt+1}/3, wait {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    _log(f"create_workitem {resp.status_code}: {resp.text[:200]}")
                    return {"error": f"DevOps {resp.status_code}: {resp.text[:200]}"}
                data = resp.json()
                break
            except httpx.TimeoutException:
                if attempt == 2:
                    return {"error": "DevOps timeout ao criar work item"}
                await asyncio.sleep(2 * (attempt + 1))
            except httpx.RequestError as e:
                if attempt == 2:
                    return {"error": f"DevOps request error ao criar work item: {str(e)}"}
                await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                return {"error": f"Erro ao criar work item: {str(e)}"}
        else:
            return {"error": "Max retries ao criar work item"}

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


async def tool_generate_chart(
    chart_type: str = "bar",
    title: str = "Chart",
    x_values: list = None,
    y_values: list = None,
    labels: list = None,
    values: list = None,
    series: list = None,
    x_label: str = "",
    y_label: str = "",
):
    """Gera um chart spec para Plotly.js. Retorna _chart no resultado."""
    chart_type = (chart_type or "bar").lower().strip()
    supported = ["bar", "pie", "line", "scatter", "histogram", "hbar"]
    if chart_type not in supported:
        chart_type = "bar"

    data = []
    layout = {
        "title": {"text": title, "font": {"size": 16}},
        "font": {"family": "Montserrat, sans-serif"},
    }

    # Multi-series via 'series' param
    if series and isinstance(series, list):
        for s in series:
            trace = {"type": s.get("type", chart_type), "name": s.get("name", "")}
            if s.get("x"): trace["x"] = s["x"]
            if s.get("y"): trace["y"] = s["y"]
            if s.get("labels"): trace["labels"] = s["labels"]
            if s.get("values"): trace["values"] = s["values"]
            if trace["type"] == "pie":
                trace.pop("x", None); trace.pop("y", None)
            data.append(trace)
    elif chart_type == "pie":
        data.append({
            "type": "pie",
            "labels": labels or x_values or [],
            "values": values or y_values or [],
            "textinfo": "label+percent",
            "hole": 0.3,
        })
    elif chart_type == "hbar":
        data.append({
            "type": "bar",
            "y": x_values or [],
            "x": y_values or [],
            "orientation": "h",
            "name": title,
        })
        layout["yaxis"] = {"title": x_label, "automargin": True}
        layout["xaxis"] = {"title": y_label}
    elif chart_type == "histogram":
        data.append({
            "type": "histogram",
            "x": x_values or y_values or [],
            "name": title,
        })
        layout["xaxis"] = {"title": x_label}
        layout["yaxis"] = {"title": y_label or "Frequência"}
    else:
        # bar, line, scatter
        data.append({
            "type": chart_type if chart_type != "bar" else "bar",
            "x": x_values or [],
            "y": y_values or [],
            "name": title,
        })
        if x_label: layout["xaxis"] = {"title": x_label}
        if y_label: layout["yaxis"] = {"title": y_label}

    chart_spec = {"data": data, "layout": layout, "config": {"responsive": True}}

    return {
        "chart_generated": True,
        "chart_type": chart_type,
        "title": title,
        "data_points": len(x_values or labels or []),
        "_chart": chart_spec,
    }

# =============================================================================
# TOOL EXECUTOR
# =============================================================================
async def execute_tool(tool_name, arguments):
    dispatch = {
        "query_workitems": lambda: tool_query_workitems(arguments.get("wiql_where",""), arguments.get("fields"), arguments.get("top",200)),
        "search_workitems": lambda: tool_search_workitems(arguments.get("query",""), arguments.get("top",30), arguments.get("filter")),
        "search_website": lambda: tool_search_website(arguments.get("query",""), arguments.get("top",10)),
        "analyze_patterns": lambda: tool_analyze_patterns_with_llm(arguments.get("created_by"), arguments.get("topic"), arguments.get("work_item_type","User Story"), arguments.get("area_path"), arguments.get("sample_size",50), arguments.get("analysis_type","template")),
        "generate_user_stories": lambda: tool_generate_user_stories(arguments.get("topic",""), arguments.get("context",""), arguments.get("num_stories",3), arguments.get("reference_area"), arguments.get("reference_author"), arguments.get("reference_topic")),
        "generate_workitem": lambda: tool_generate_user_stories(arguments.get("topic",""), arguments.get("requirements",""), reference_area=arguments.get("reference_area"), reference_author=arguments.get("reference_author")),
        "query_hierarchy": lambda: tool_query_hierarchy(arguments.get("parent_id"), arguments.get("parent_type","Epic"), arguments.get("child_type","User Story"), arguments.get("area_path")),
        "compute_kpi": lambda: tool_compute_kpi(arguments.get("wiql_where",""), arguments.get("group_by"), arguments.get("kpi_type","count")),
        "create_workitem": lambda: tool_create_workitem(
            arguments.get("work_item_type", "User Story"),
            arguments.get("title", ""),
            arguments.get("description", ""),
            arguments.get("acceptance_criteria", ""),
            arguments.get("area_path", ""),
            arguments.get("assigned_to", ""),
            arguments.get("tags", ""),
            arguments.get("confirmed", False),
        ),
        "generate_chart": lambda: tool_generate_chart(
            arguments.get("chart_type", "bar"),
            arguments.get("title", "Chart"),
            arguments.get("x_values"),
            arguments.get("y_values"),
            arguments.get("labels"),
            arguments.get("values"),
            arguments.get("series"),
            arguments.get("x_label", ""),
            arguments.get("y_label", ""),
        ),
    }
    fn = dispatch.get(tool_name)
    return await fn() if fn else {"error": f"Tool desconhecida: {tool_name}"}

def truncate_tool_result(result_str):
    if len(result_str) <= AGENT_TOOL_RESULT_MAX_SIZE: return result_str
    try:
        data = json.loads(result_str)
        if isinstance(data, dict) and "items" in data:
            data["items"] = data["items"][:AGENT_TOOL_RESULT_KEEP_ITEMS]; data["_truncated"]=True; data["_original_items"]=len(data.get("items",[]))
            return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logging.warning("[Tools] truncate_tool_result fallback: %s", e)
    return result_str[:AGENT_TOOL_RESULT_MAX_SIZE] + "\n...(truncado)"

# =============================================================================
# TOOL DEFINITIONS (formato OpenAI — traduzido auto para Anthropic pelo llm_provider)
# =============================================================================
TOOLS = [
    {"type":"function","function":{"name":"query_workitems","description":"Query Azure DevOps via WIQL para contagens, listagens, filtros. Dados em TEMPO REAL.","parameters":{"type":"object","properties":{"wiql_where":{"type":"string","description":"WHERE WIQL. Ex: [System.WorkItemType]='User Story' AND [System.State]='Active'"},"fields":{"type":"array","items":{"type":"string"},"description":"Campos extra a retornar. Default: Id,Title,State,Type,AssignedTo,CreatedBy,AreaPath,CreatedDate. Adicionar 'System.Description' e 'Microsoft.VSTS.Common.AcceptanceCriteria' quando o user pedir detalhes/descrição/AC."},"top":{"type":"integer","description":"Max resultados. 0=só contagem."}},"required":["wiql_where"]}}},
    {"type":"function","function":{"name":"search_workitems","description":"Pesquisa semântica em work items indexados. Retorna AMOSTRA dos mais relevantes.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto. Ex: 'transferências SPIN'"},"top":{"type":"integer","description":"Nº resultados. Default: 30."},"filter":{"type":"string","description":"Filtro OData."}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_website","description":"Pesquisa no site MSE. Usa para navegação, funcionalidades, operações.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto. Ex: 'transferência SEPA'"},"top":{"type":"integer","description":"Default: 10"}},"required":["query"]}}},
    {"type":"function","function":{"name":"analyze_patterns","description":"Analisa padrões de escrita de work items com LLM. Templates, estilo de autor.","parameters":{"type":"object","properties":{"created_by":{"type":"string"},"topic":{"type":"string"},"work_item_type":{"type":"string","description":"Default: 'User Story'"},"area_path":{"type":"string"},"sample_size":{"type":"integer","description":"Default: 50"},"analysis_type":{"type":"string","description":"'template','author_style','general'"}}}}},
    {"type":"function","function":{"name":"generate_user_stories","description":"Gera USs NOVAS baseadas em padrões reais. USA SEMPRE quando pedirem criar/gerar USs.","parameters":{"type":"object","properties":{"topic":{"type":"string","description":"Tema das USs."},"context":{"type":"string","description":"Contexto: Miro, Figma, requisitos."},"num_stories":{"type":"integer","description":"Nº USs. Default: 3."},"reference_area":{"type":"string"},"reference_author":{"type":"string"},"reference_topic":{"type":"string"}},"required":["topic"]}}},
    {"type":"function","function":{"name":"query_hierarchy","description":"Query hierárquica parent/child. OBRIGATÓRIO para 'Epic', 'dentro de', 'filhos de'.","parameters":{"type":"object","properties":{"parent_id":{"type":"integer","description":"ID do pai."},"parent_type":{"type":"string","description":"Default: 'Epic'."},"child_type":{"type":"string","description":"Default: 'User Story'."},"area_path":{"type":"string"}}}}},
    {"type":"function","function":{"name":"compute_kpi","description":"Calcula KPIs (até 1000 items). OBRIGATÓRIO para rankings, distribuições, tendências.","parameters":{"type":"object","properties":{"wiql_where":{"type":"string"},"group_by":{"type":"string","description":"'state','type','assigned_to','created_by','area'"},"kpi_type":{"type":"string","description":"'count','timeline','distribution'"}},"required":["wiql_where"]}}},
    {"type":"function","function":{"name":"create_workitem","description":"Cria um Work Item no Azure DevOps. USA APENAS quando o utilizador CONFIRMAR explicitamente a criação. PERGUNTA SEMPRE antes de criar.","parameters":{"type":"object","properties":{"work_item_type":{"type":"string","description":"Tipo: 'User Story', 'Bug', 'Task', 'Feature'. Default: 'User Story'."},"title":{"type":"string","description":"Título do Work Item."},"description":{"type":"string","description":"Descrição em HTML. Usa formato MSE."},"acceptance_criteria":{"type":"string","description":"Critérios de aceitação em HTML."},"area_path":{"type":"string","description":"AreaPath. Ex: 'IT.DIT\\\\DIT\\\\ADMChannels\\\\DBKS\\\\AM24\\\\RevampFEE MVP2'"},"assigned_to":{"type":"string","description":"Nome completo da pessoa. Ex: 'Pedro Mousinho'"},"tags":{"type":"string","description":"Tags separadas por ';'. Ex: 'MVP2;FEE;Sprint23'"},"confirmed":{"type":"boolean","description":"true apenas após confirmação explícita do utilizador (ex: 'confirmo')."}},"required":["title"]}}},
    {"type":"function","function":{"name":"generate_chart","description":"Gera gráfico interativo (bar, pie, line, scatter, histogram, hbar). USA SEMPRE que o utilizador pedir gráfico, chart, visualização ou distribuição visual. Extrai dados de tool_results anteriores ou de dados fornecidos.","parameters":{"type":"object","properties":{"chart_type":{"type":"string","description":"Tipo: 'bar','pie','line','scatter','histogram','hbar'. Default: 'bar'."},"title":{"type":"string","description":"Título do gráfico."},"x_values":{"type":"array","items":{"type":"string"},"description":"Valores eixo X (categorias ou datas). Ex: ['Active','Closed','New']"},"y_values":{"type":"array","items":{"type":"number"},"description":"Valores eixo Y (numéricos). Ex: [45, 30, 12]"},"labels":{"type":"array","items":{"type":"string"},"description":"Labels para pie chart. Ex: ['Bug','US','Task']"},"values":{"type":"array","items":{"type":"number"},"description":"Valores para pie chart. Ex: [20, 50, 30]"},"series":{"type":"array","items":{"type":"object"},"description":"Multi-series. Cada obj: {type,name,x,y,labels,values}"},"x_label":{"type":"string","description":"Label do eixo X"},"y_label":{"type":"string","description":"Label do eixo Y"}},"required":["title"]}}},
]

# =============================================================================
# SYSTEM PROMPTS
# =============================================================================
def get_agent_system_prompt():
    return f"""Tu és o Assistente IA do Millennium BCP para a equipa de desenvolvimento DIT/ADMChannels.
Tens acesso a ferramentas para consultar dados reais do Azure DevOps e do site MSE.

DATA ACTUAL: {datetime.now().strftime('%Y-%m-%d')} (usa esta data como referência para queries temporais)

REGRAS DE CLARIFICAÇÃO (IMPORTANTE):
- Se a pergunta do utilizador mencionar um NOME DE PESSOA que pode corresponder a múltiplas pessoas, DEVES perguntar qual pessoa antes de executar. Isto é OBRIGATÓRIO.
- Exemplos de quando PERGUNTAR (OBRIGATÓRIO):
  • Só primeiro nome: "mostra o que o Jorge criou" → PERGUNTA "Queres dizer Jorge Eduardo Rodrigues, ou outro Jorge? Indica o nome completo."
  • Nome parcial ambíguo: "bugs do Pedro" → PERGUNTA "Qual Pedro? Pedro Mousinho, Pedro Silva, ou outro?"
- Exemplos de quando NÃO perguntar (responde diretamente):
  • Nome completo fornecido: "bugs do Jorge Eduardo Rodrigues" → executa imediatamente
  • A intenção é clara sem ambiguidade: "quantas user stories em 2025" → executa imediatamente
- REGRA: Para NOMES DE PESSOAS, pergunta sempre que o nome não seja completo. Para tudo o resto, na dúvida EXECUTA.

NOMES NO AZURE DEVOPS:
- Os nomes no DevOps são nomes completos (ex: "Jorge Eduardo Rodrigues", não "Jorge Rodrigues")
- Quando usares Contains para nomes, usa APENAS o primeiro nome OU o nome completo confirmado

REGRA PRIORITÁRIA — RESPOSTA DIRECTA SEM FERRAMENTAS:
Antes de decidir qual ferramenta usar, avalia se a pergunta PRECISA de dados do DevOps, AI Search ou site MSE.
Se NÃO precisa, responde DIRETAMENTE sem chamar nenhuma ferramenta.

Categorias que NÃO precisam de ferramentas (responde directamente):
1. CONCEPTUAL/EDUCATIVO: "O que é uma user story?", "Explica WIQL", "Diferença entre Epic e Feature", "Boas práticas de Agile"
2. REDACÇÃO E ESCRITA: "Escreve-me um email para...", "Ajuda-me a redigir...", "Resume este texto", "Traduz isto para inglês"
3. OPINIÃO/CONSELHO: "Qual a melhor forma de organizar sprints?", "Achas que devia dividir esta US?"
4. CONVERSAÇÃO: Saudações, agradecimentos, perguntas sobre ti próprio, clarificações sobre respostas anteriores
5. ANÁLISE DE CONTEÚDO FORNECIDO: Quando o utilizador cola texto/dados directamente no chat e pede análise, resumo ou reformulação — os dados JÁ ESTÃO na mensagem, não precisas de os ir buscar
6. DOCUMENTAÇÃO E TEMPLATES: "Dá-me um template de Definition of Ready", "Como se estrutura um AC?"

REGRA: Na dúvida entre responder directamente ou usar ferramenta, prefere responder directamente.
Só usa ferramentas quando precisas de dados ESPECÍFICOS que não tens no contexto da conversa.

ROUTING SIMULTÂNEO (IMPORTANTE):
- Podes e DEVES chamar MÚLTIPLAS ferramentas EM PARALELO quando a pergunta precisa de dados de fontes diferentes.
- Chama todas as ferramentas necessárias de uma vez — NÃO esperes pela resposta de uma para chamar a outra quando são independentes.

REGRAS DE ROUTING (decide qual ferramenta usar):
1. Para CONTAGENS, LISTAGENS ou FILTROS EXATOS → usa query_workitems (WIQL direto ao Azure DevOps)
   Exemplos: "quantas USs existem", "lista bugs ativos", "USs criadas em janeiro"
2. Para PESQUISA SEMÂNTICA por tópico/similaridade → usa search_workitems (busca vetorial)
   Exemplos: "USs sobre transferências SPIN", "bugs relacionados com timeout"
   NOTA: Retorna os mais RELEVANTES, não TODOS. Diz sempre "resultados mais relevantes".
3. Para perguntas sobre o SITE/APP MSE → usa search_website (busca no conteúdo web)
4. Para ANÁLISE DE PADRÕES de escrita → usa analyze_patterns (busca exemplos + análise LLM)
5. Para GERAR NOVOS WORK ITEMS → usa generate_user_stories (busca exemplos + gera no mesmo padrão)
6. Para HIERARQUIAS (Epic→Feature→US→Task) → usa query_hierarchy (OBRIGATÓRIO)
   Exemplos: "USs dentro do Epic 12345", "filhos do Feature X"
   REGRA: Sempre que o utilizador mencionar "Epic", "dentro de", "filhos de" → query_hierarchy
7. Para KPIs, RANKINGS, DISTRIBUIÇÕES, ANÁLISE → usa compute_kpi (OBRIGATÓRIO)
   Exemplos: "quem criou mais USs", "distribuição por estado", "top contributors"
   REGRA: Sempre que o utilizador pedir ranking, comparação, tendência → compute_kpi
8. Para CRIAR WORK ITEMS no board → usa create_workitem (OBRIGATÓRIO)
   Exemplos: "cria esta US no DevOps", "coloca no board", "adiciona ao backlog"
   REGRA CRÍTICA: NUNCA criar sem confirmação explícita do utilizador.
   Fluxo: 1) Gerar/mostrar conteúdo → 2) Perguntar "Confirmas a criação?" → 3) Só criar após "sim/confirmo"
9. Para GRÁFICOS, CHARTS, VISUALIZAÇÕES → usa generate_chart (OBRIGATÓRIO)
   Exemplos: "mostra um gráfico de bugs por estado", "chart de USs por mês", "visualiza a distribuição"
   REGRA: Primeiro obtém os dados (query_workitems/compute_kpi), depois chama generate_chart com os valores extraídos.
   REGRA: Podes chamar compute_kpi + generate_chart em sequência (não em paralelo — precisas dos dados primeiro).

QUANDO USAR query_workitems vs search_workitems vs compute_kpi (IMPORTANTE):
- "Quantas USs existem no RevampFEE?" → query_workitems com top=0 (contagem rápida)
- "Quais USs falam sobre pagamentos?" → search_workitems (semântica)
- "Lista TODAS as USs com 'SPIN' no título" → query_workitems com CONTAINS e top=1000
- "Quem criou mais USs em 2025?" → compute_kpi com group_by="created_by"
- "USs do Epic 12345" → query_hierarchy com parent_id=12345
- "Distribuição de estados no MDSE" → compute_kpi com kpi_type="distribution"
- Para CRIAR → usa create_workitem (pede SEMPRE confirmação)
- "Mostra gráfico de bugs por estado" → compute_kpi DEPOIS generate_chart
- "Visualiza distribuição de USs" → compute_kpi DEPOIS generate_chart

CAMPOS ESPECIAIS (IMPORTANTE):
- Para obter DESCRIÇÃO ou CRITÉRIOS DE ACEITAÇÃO, inclui fields: ["System.Id","System.Title","System.State","System.WorkItemType","System.Description","Microsoft.VSTS.Common.AcceptanceCriteria"]
- Default sem esses campos é suficiente para listagens/contagens

REGRA ANTI-CRASH (IMPORTANTE):
- Se uma ferramenta retornar erro, NÃO entres em pânico. Explica o erro ao utilizador e sugere alternativa.
- Se retornar muitos dados truncados, diz quantos existem no total e mostra os que tens.
- NUNCA chames a mesma ferramenta com os mesmos argumentos duas vezes seguidas.

RESPOSTA: PT-PT. IDs: [US 912700]. Links DevOps. Contagens EXATAS com total_count. Tabelas markdown quando apropriado. Parágrafos naturais.

ÁREAS: RevampFEE MVP2, MDSE, ACEDigital, MSE (sob IT.DIT\\DIT\\ADMChannels\\DBKS\\AM24)
TIPOS: User Story, Bug, Task, Feature, Epic
ESTADOS: New, Active, Closed, Resolved, Removed
CAMPOS WIQL: System.WorkItemType, State, AreaPath, Title (CONTAINS), AssignedTo, CreatedBy, CreatedDate ('YYYY-MM-DD'), ChangedDate, Tags
- [Microsoft.VSTS.Common.AcceptanceCriteria]

EXEMPLOS DE WIQL:
- USs criadas em 2025: [System.CreatedDate] >= '2025-01-01' AND [System.CreatedDate] < '2026-01-01'
- Para "quem criou mais", query SEM filtro de criador, top=500, conta por created_by"""

def get_userstory_system_prompt():
    return f"""Tu és PO Sénior especialista no MSE (Millennium Site Empresas).
Objetivo: transformar pedidos em User Stories rigorosas.
DATA: {datetime.now().strftime('%Y-%m-%d')}

USA SEMPRE generate_user_stories para buscar exemplos reais primeiro.
REGRA DE OURO: Aprende granularidade e vocabulário dos exemplos. HTML limpo (<b>,<ul>,<li>,<br>,<div>).

INPUT: Texto→essência funcional. Imagens→CTAs,Inputs,Labels. Ficheiros→requisitos. Miro→decompõe. Figma→componentes.
VOCABULÁRIO: CTA, Enable/Disable, Input, Dropdown, Stepper, Toast, Modal, FEE, Header

ESTRUTURA:
Título: MSE | [Área] | [Sub-área] | [Funcionalidade] | [Detalhe]
Descrição: <div>Eu como <b>[Persona]</b> quero [ação] para que [benefício].</div>
AC: Objetivo/Âmbito, Composição, Comportamento, Mockup

PT-PT. Auto-contida, testável, granular. Dúvidas → pergunta.
ÁREAS: RevampFEE MVP2, MDSE, ACEDigital, MSE"""
