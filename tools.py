# =============================================================================
# tools.py — Fachada de compatibilidade (reexporta módulos de domínio)
# =============================================================================
import logging
from datetime import datetime, timezone
from config import WEB_SEARCH_ENABLED
from tools_devops import (
    get_devops_debug_log,
    _devops_headers,
    _devops_url,
    tool_query_workitems,
    tool_query_hierarchy,
    tool_compute_kpi,
    tool_create_workitem,
    tool_refine_workitem,
    tool_analyze_patterns,
    tool_analyze_patterns_with_llm,
    tool_generate_user_stories,
)
from tools_knowledge import (
    get_embedding,
    tool_search_workitems,
    tool_search_website,
    tool_search_web,
)
from tools_export import (
    get_generated_file,
    _store_generated_file,
    truncate_tool_result,
    tool_generate_chart,
    tool_generate_file,
)
from tools_upload import tool_search_uploaded_document
from tools_learning import (
    _save_writer_profile,
    _load_writer_profile,
)
from http_helpers import (
    devops_request_with_retry as _devops_request_with_retry,
    search_request_with_retry,
)
# Backward-compatible aliases used by existing imports.
_search_request_with_retry = search_request_with_retry
# Tool definitions, registration, dispatch, system prompts
# (wiring central mantém-se aqui)
from tool_registry import (
    register_tool,
    has_tool,
    execute_tool as registry_execute_tool,
    get_all_tool_definitions as registry_get_all_tool_definitions,
)
_BUILTIN_TOOL_DEFINITIONS = [
    {"type":"function","function":{"name":"query_workitems","description":"Query Azure DevOps via WIQL para contagens, listagens, filtros. Dados em TEMPO REAL.","parameters":{"type":"object","properties":{"wiql_where":{"type":"string","description":"WHERE WIQL. Ex: [System.WorkItemType]='User Story' AND [System.State]='Active'"},"fields":{"type":"array","items":{"type":"string"},"description":"Campos extra a retornar. Default: Id,Title,State,Type,AssignedTo,CreatedBy,AreaPath,CreatedDate. Adicionar 'System.Description' e 'Microsoft.VSTS.Common.AcceptanceCriteria' quando o user pedir detalhes/descrição/AC."},"top":{"type":"integer","description":"Max resultados. 0=só contagem."}},"required":["wiql_where"]}}},
    {"type":"function","function":{"name":"search_workitems","description":"Pesquisa semântica em work items indexados. Retorna AMOSTRA dos mais relevantes.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto. Ex: 'transferências SPIN'"},"top":{"type":"integer","description":"Nº resultados. Default: 30."},"filter":{"type":"string","description":"Filtro OData."}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_website","description":"Pesquisa no site MSE. Usa para navegação, funcionalidades, operações.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto. Ex: 'transferência SEPA'"},"top":{"type":"integer","description":"Default: 10"}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_uploaded_document","description":"Pesquisa semântica no documento carregado pelo utilizador. Usar quando o utilizador perguntar sobre conteúdos específicos de um documento que fez upload e o documento é grande.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Texto a pesquisar semanticamente no documento carregado."},"conv_id":{"type":"string","description":"ID da conversa. Opcional; se vazio, tenta inferir automaticamente."}},"required":["query"]}}},
    {"type":"function","function":{"name":"analyze_patterns","description":"Analisa padrões de escrita de work items com LLM. Templates, estilo de autor.","parameters":{"type":"object","properties":{"created_by":{"type":"string"},"topic":{"type":"string"},"work_item_type":{"type":"string","description":"Default: 'User Story'"},"area_path":{"type":"string"},"sample_size":{"type":"integer","description":"Default: 50"},"analysis_type":{"type":"string","description":"'template','author_style','general'"}}}}},
    {"type":"function","function":{"name":"generate_user_stories","description":"Gera USs NOVAS baseadas em padrões reais. USA SEMPRE quando pedirem criar/gerar USs.","parameters":{"type":"object","properties":{"topic":{"type":"string","description":"Tema das USs."},"context":{"type":"string","description":"Contexto: Miro, Figma, requisitos."},"num_stories":{"type":"integer","description":"Nº USs. Default: 3."},"reference_area":{"type":"string"},"reference_author":{"type":"string"},"reference_topic":{"type":"string"}},"required":["topic"]}}},
    {"type":"function","function":{"name":"query_hierarchy","description":"Query hierárquica parent/child. OBRIGATÓRIO para 'Epic', 'dentro de', 'filhos de'.","parameters":{"type":"object","properties":{"parent_id":{"type":"integer","description":"ID do pai."},"parent_type":{"type":"string","description":"Default: 'Epic'."},"child_type":{"type":"string","description":"Default: 'User Story'."},"area_path":{"type":"string"},"title_contains":{"type":"string","description":"Filtro opcional por título (contains, case/accent-insensitive). Ex: 'Créditos Consultar Carteira'"},"parent_title_hint":{"type":"string","description":"(Interno) dica de título do parent para resolução quando parent_id não for fornecido."}}}}},
    {"type":"function","function":{"name":"compute_kpi","description":"Calcula KPIs (até 1000 items). OBRIGATÓRIO para rankings, distribuições, tendências.","parameters":{"type":"object","properties":{"wiql_where":{"type":"string"},"group_by":{"type":"string","description":"'state','type','assigned_to','created_by','area'"},"kpi_type":{"type":"string","description":"'count','timeline','distribution'"}},"required":["wiql_where"]}}},
    {"type":"function","function":{"name":"create_workitem","description":"Cria um Work Item no Azure DevOps. USA APENAS quando o utilizador CONFIRMAR explicitamente a criação. PERGUNTA SEMPRE antes de criar.","parameters":{"type":"object","properties":{"work_item_type":{"type":"string","description":"Tipo: 'User Story', 'Bug', 'Task', 'Feature'. Default: 'User Story'."},"title":{"type":"string","description":"Título do Work Item."},"description":{"type":"string","description":"Descrição em HTML. Usa formato MSE."},"acceptance_criteria":{"type":"string","description":"Critérios de aceitação em HTML."},"area_path":{"type":"string","description":"AreaPath. Ex: 'IT.DIT\\\\DIT\\\\ADMChannels\\\\DBKS\\\\AM24\\\\RevampFEE MVP2'"},"assigned_to":{"type":"string","description":"Nome completo da pessoa. Ex: 'Pedro Mousinho'"},"tags":{"type":"string","description":"Tags separadas por ';'. Ex: 'MVP2;FEE;Sprint23'"},"confirmed":{"type":"boolean","description":"true apenas após confirmação explícita do utilizador (ex: 'confirmo')."}},"required":["title"]}}},
    {"type":"function","function":{"name":"refine_workitem","description":"Refina uma User Story existente no DevOps a partir de uma instrução curta (sem alterar automaticamente o item). Usa quando o utilizador pedir ajustes numa US já criada, ex: 'na US 12345 adiciona validação de email'.","parameters":{"type":"object","properties":{"work_item_id":{"type":"integer","description":"ID do work item existente a refinar."},"refinement_request":{"type":"string","description":"Instrução objetiva do que mudar na US existente."}},"required":["work_item_id","refinement_request"]}}},
    {"type":"function","function":{"name":"generate_chart","description":"Gera gráfico interativo (bar, pie, line, scatter, histogram, hbar). USA SEMPRE que o utilizador pedir gráfico, chart, visualização ou distribuição visual. Extrai dados de tool_results anteriores ou de dados fornecidos.","parameters":{"type":"object","properties":{"chart_type":{"type":"string","description":"Tipo: 'bar','pie','line','scatter','histogram','hbar'. Default: 'bar'."},"title":{"type":"string","description":"Título do gráfico."},"x_values":{"type":"array","items":{"type":"string"},"description":"Valores eixo X (categorias ou datas). Ex: ['Active','Closed','New']"},"y_values":{"type":"array","items":{"type":"number"},"description":"Valores eixo Y (numéricos). Ex: [45, 30, 12]"},"labels":{"type":"array","items":{"type":"string"},"description":"Labels para pie chart. Ex: ['Bug','US','Task']"},"values":{"type":"array","items":{"type":"number"},"description":"Valores para pie chart. Ex: [20, 50, 30]"},"series":{"type":"array","items":{"type":"object"},"description":"Multi-series. Cada obj: {type,name,x,y,labels,values}"},"x_label":{"type":"string","description":"Label do eixo X"},"y_label":{"type":"string","description":"Label do eixo Y"}},"required":["title"]}}},
    {"type":"function","function":{"name":"generate_file","description":"Gera ficheiro para download (CSV, XLSX, PDF, DOCX) quando o utilizador pedir explicitamente para gerar/descarregar ficheiro com dados.","parameters":{"type":"object","properties":{"format":{"type":"string","enum":["csv","xlsx","pdf","docx"],"description":"Formato do ficheiro a gerar."},"title":{"type":"string","description":"Título/nome base do ficheiro."},"data":{"type":"array","items":{"type":"object"},"description":"Linhas de dados (array de objetos)."},"columns":{"type":"array","items":{"type":"string"},"description":"Headers/ordem das colunas no ficheiro."}},"required":["format","title","data","columns"]}}},
]

if WEB_SEARCH_ENABLED:
    _BUILTIN_TOOL_DEFINITIONS.append(
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Pesquisa na web (Bing). Usar quando o utilizador pedir informação actual/externa que não está no DevOps nem no site MSE.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Texto de pesquisa web."},
                        "top": {"type": "integer", "description": "Número de resultados. Default: 5."},
                    },
                    "required": ["query"],
                },
            },
        }
    )

_TOOL_DEFINITION_BY_NAME = {
    d.get("function", {}).get("name"): d
    for d in _BUILTIN_TOOL_DEFINITIONS
    if d.get("function", {}).get("name")
}


def _tool_dispatch() -> dict:
    dispatch = {
        "query_workitems": lambda arguments: tool_query_workitems(arguments.get("wiql_where",""), arguments.get("fields"), arguments.get("top",200)),
        "search_workitems": lambda arguments: tool_search_workitems(arguments.get("query",""), arguments.get("top",30), arguments.get("filter")),
        "search_website": lambda arguments: tool_search_website(arguments.get("query",""), arguments.get("top",10)),
        "search_uploaded_document": lambda arguments: tool_search_uploaded_document(
            arguments.get("query", ""),
            arguments.get("conv_id", ""),
            arguments.get("user_sub", ""),
        ),
        "analyze_patterns": lambda arguments: tool_analyze_patterns_with_llm(arguments.get("created_by"), arguments.get("topic"), arguments.get("work_item_type","User Story"), arguments.get("area_path"), arguments.get("sample_size",50), arguments.get("analysis_type","template")),
        "generate_user_stories": lambda arguments: tool_generate_user_stories(arguments.get("topic",""), arguments.get("context",""), arguments.get("num_stories",3), arguments.get("reference_area"), arguments.get("reference_author"), arguments.get("reference_topic")),
        "generate_workitem": lambda arguments: tool_generate_user_stories(arguments.get("topic",""), arguments.get("requirements",""), reference_area=arguments.get("reference_area"), reference_author=arguments.get("reference_author")),
        "query_hierarchy": lambda arguments: tool_query_hierarchy(
            arguments.get("parent_id"),
            arguments.get("parent_type", "Epic"),
            arguments.get("child_type", "User Story"),
            arguments.get("area_path"),
            arguments.get("title_contains"),
            arguments.get("parent_title_hint"),
        ),
        "compute_kpi": lambda arguments: tool_compute_kpi(arguments.get("wiql_where",""), arguments.get("group_by"), arguments.get("kpi_type","count")),
        "create_workitem": lambda arguments: tool_create_workitem(
            arguments.get("work_item_type", "User Story"),
            arguments.get("title", ""),
            arguments.get("description", ""),
            arguments.get("acceptance_criteria", ""),
            arguments.get("area_path", ""),
            arguments.get("assigned_to", ""),
            arguments.get("tags", ""),
            arguments.get("confirmed", False),
        ),
        "refine_workitem": lambda arguments: tool_refine_workitem(
            arguments.get("work_item_id", 0),
            arguments.get("refinement_request", ""),
        ),
        "generate_chart": lambda arguments: tool_generate_chart(
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
        "generate_file": lambda arguments: tool_generate_file(
            arguments.get("format", "csv"),
            arguments.get("title", "Export"),
            arguments.get("data"),
            arguments.get("columns"),
        ),
    }
    if WEB_SEARCH_ENABLED:
        dispatch["search_web"] = lambda arguments: tool_search_web(
            arguments.get("query", ""),
            arguments.get("top", 5),
        )
    return dispatch


def _register_builtin_tools() -> None:
    dispatch = _tool_dispatch()
    for tool_name, handler in dispatch.items():
        definition = _TOOL_DEFINITION_BY_NAME.get(tool_name)
        register_tool(tool_name, handler, definition=definition)


_register_builtin_tools()

# Optional integrations (registo explícito sem side-effects de import).
try:
    from tools_figma import _register_figma_tool

    _register_figma_tool()
except Exception:
    logging.exception("[Tools] optional module tools_figma failed to load/register")

try:
    from tools_miro import _register_miro_tool

    _register_miro_tool()
except Exception:
    logging.exception("[Tools] optional module tools_miro failed to load/register")


_SEARCH_FIGMA_PROXY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_figma",
        "description": "Pesquisa no Figma (read-only). Usa quando o utilizador mencionar designs, mockups, ecras, UI ou prototipos.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto de pesquisa em nomes de ficheiro/frame."},
                "file_key": {"type": "string", "description": "Figma file key para detalhar um ficheiro especifico."},
                "node_id": {"type": "string", "description": "Node/frame id para detalhe especifico dentro do ficheiro."},
            },
        },
    },
}

_SEARCH_MIRO_PROXY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_miro",
        "description": "Pesquisa no Miro (read-only). Usa quando o utilizador mencionar workshops, brainstorms, boards, sticky notes ou planning sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto de pesquisa para boards/conteudo."},
                "board_id": {"type": "string", "description": "Board id para detalhar conteudo desse board."},
            },
        },
    },
}


async def _search_figma_proxy(arguments):
    try:
        from tools_figma import tool_search_figma

        return await tool_search_figma(
            query=(arguments or {}).get("query", ""),
            file_key=(arguments or {}).get("file_key", ""),
            node_id=(arguments or {}).get("node_id", ""),
        )
    except Exception as e:
        logging.error("[Tools] search_figma proxy failed: %s", e, exc_info=True)
        return {"error": "Integração Figma indisponível neste runtime"}


async def _search_miro_proxy(arguments):
    try:
        from tools_miro import tool_search_miro

        return await tool_search_miro(
            query=(arguments or {}).get("query", ""),
            board_id=(arguments or {}).get("board_id", ""),
        )
    except Exception as e:
        logging.error("[Tools] search_miro proxy failed: %s", e, exc_info=True)
        return {"error": "Integração Miro indisponível neste runtime"}


def _ensure_optional_tool_proxies() -> None:
    """Garante presença de tools opcionais no registry mesmo com falhas de import."""
    if not has_tool("search_figma"):
        register_tool(
            "search_figma",
            lambda args: _search_figma_proxy(args),
            definition=_SEARCH_FIGMA_PROXY_DEFINITION,
        )
        logging.warning("[Tools] search_figma registada via proxy fallback")

    if not has_tool("search_miro"):
        register_tool(
            "search_miro",
            lambda args: _search_miro_proxy(args),
            definition=_SEARCH_MIRO_PROXY_DEFINITION,
        )
        logging.warning("[Tools] search_miro registada via proxy fallback")


_ensure_optional_tool_proxies()


async def execute_tool(tool_name, arguments):
    """Compat wrapper; execução real vive no tool_registry."""
    return await registry_execute_tool(tool_name, arguments)


def get_all_tool_definitions():
    return registry_get_all_tool_definitions()


# Compatibilidade com código antigo que ainda importa TOOLS.
TOOLS = get_all_tool_definitions()

# =============================================================================
# SYSTEM PROMPTS
# =============================================================================
# v7.2.8 (Fase G): Melhorias no routing, qualidade de resposta e sintese RAG
def get_agent_system_prompt():
    figma_enabled = has_tool("search_figma")
    miro_enabled = has_tool("search_miro")
    uploaded_doc_enabled = has_tool("search_uploaded_document")

    def _join_with_ou(parts):
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " ou " + parts[-1]

    data_sources = ["DevOps", "AI Search", "site MSE"]
    if WEB_SEARCH_ENABLED:
        data_sources.append("web externa")
    if uploaded_doc_enabled:
        data_sources.append("documento carregado")
    if figma_enabled:
        data_sources.append("Figma")
    if miro_enabled:
        data_sources.append("Miro")
    data_sources_text = _join_with_ou(data_sources)

    gate_priority_hints = []
    if uploaded_doc_enabled:
        gate_priority_hints.append(
            "- Se o utilizador perguntar sobre secções específicas de documento carregado (especialmente PDF grande), usa search_uploaded_document."
        )
    if figma_enabled:
        gate_priority_hints.append(
            "- Se o utilizador mencionar Figma, design, mockup, ecras UI ou prototipos, usa search_figma (nao responder diretamente)."
        )
    if miro_enabled:
        gate_priority_hints.append(
            "- Se o utilizador mencionar Miro, board, workshop, brainstorm ou sticky notes, usa search_miro (nao responder diretamente)."
        )
    gate_priority_hints_text = "\n".join(gate_priority_hints)
    exception_targets = []
    if uploaded_doc_enabled:
        exception_targets.append("documento carregado")
    if figma_enabled:
        exception_targets.append("Figma")
    if miro_enabled:
        exception_targets.append("Miro")
    exception_priority_line = ""
    if exception_targets:
        exception_priority_line = (
            "EXCEÇÃO PRIORITÁRIA: pedidos sobre "
            f"{_join_with_ou(exception_targets)} DEVEM usar as respetivas tools quando estiverem ativas."
        )

    routing_rules = [
        "1. Para CONTAGENS, LISTAGENS ou FILTROS EXATOS -> usa query_workitems (WIQL direto ao Azure DevOps)\n"
        "   Exemplos: \"quantas USs existem\", \"lista bugs ativos\", \"USs criadas em janeiro\"",
        "2. Para PESQUISA SEMANTICA por topico/similaridade -> usa search_workitems (busca vetorial)\n"
        "   Exemplos: \"USs sobre transferencias SPIN\", \"bugs relacionados com timeout\"\n"
        "   NOTA: Retorna os mais RELEVANTES, nao TODOS. Diz sempre \"resultados mais relevantes\".",
        "3. Para perguntas sobre o SITE/APP MSE -> usa search_website (busca no conteudo web)",
        "4. Para ANALISE DE PADROES de escrita -> usa analyze_patterns (busca exemplos + analise LLM)",
        "5. Para GERAR NOVOS WORK ITEMS -> usa generate_user_stories (busca exemplos + gera no mesmo padrao)",
        "6. Para HIERARQUIAS (Epic->Feature->US->Task) -> usa query_hierarchy (OBRIGATORIO)\n"
        "   Exemplos: \"USs dentro do Epic 12345\", \"filhos do Feature X\"\n"
        "   REGRA: Sempre que o utilizador mencionar \"Epic\", \"dentro de\", \"filhos de\" -> query_hierarchy\n"
        "   REGRA: Se pedir filtro por título (ex: \"cujo título tem ...\"), preencher title_contains.\n"
        "   REGRA: Se o pedido tiver múltiplas hierarquias (ex: bugs do Epic X E US da Feature Y), fazer múltiplas chamadas query_hierarchy e combinar.\n"
        "   REGRA: query_hierarchy devolve lista EXATA (não semântica). Nunca dizer \"mais relevantes\".\n"
        "   REGRA: Se total_count <= 100, listar TODOS os itens devolvidos.",
        "7. Para KPIs, RANKINGS, DISTRIBUICOES, ANALISE -> usa compute_kpi (OBRIGATORIO)\n"
        "   Exemplos: \"quem criou mais USs\", \"distribuicao por estado\", \"top contributors\"\n"
        "   REGRA: Sempre que o utilizador pedir ranking, comparacao, tendencia -> compute_kpi",
        "8. Para CRIAR WORK ITEMS no board -> usa create_workitem (OBRIGATORIO)\n"
        "   Exemplos: \"cria esta US no DevOps\", \"coloca no board\", \"adiciona ao backlog\"\n"
        "   REGRA CRITICA: NUNCA criar sem confirmacao explicita do utilizador.\n"
        "   Fluxo: 1) Gerar/mostrar conteudo -> 2) Perguntar \"Confirmas a criacao?\" -> 3) So criar apos \"sim/confirmo\"",
        "9. Para REFINAR/ATUALIZAR US EXISTENTE por ID -> usa refine_workitem (OBRIGATORIO)\n"
        "   Exemplos: \"na US 912345 adiciona validacao de email\", \"ajusta a US 800123 para incluir toast de sucesso\"\n"
        "   REGRA: Primeiro apresenta DRAFT revisto e pede validacao antes de qualquer criacao derivada.",
        "10. Para GRAFICOS, CHARTS, VISUALIZACOES -> usa generate_chart (OBRIGATORIO)\n"
        "   Exemplos: \"mostra um grafico de bugs por estado\", \"chart de USs por mes\", \"visualiza a distribuicao\"\n"
        "   REGRA: Primeiro obtem os dados (query_workitems/compute_kpi), depois chama generate_chart com os valores extraidos.\n"
        "   REGRA: Podes chamar compute_kpi + generate_chart em sequencia (nao em paralelo - precisas dos dados primeiro).",
        "11. Para GERAR ou DESCARREGAR ficheiros (Excel/CSV/PDF/DOCX) com dados -> usa generate_file (OBRIGATORIO)\n"
        "   Exemplos: \"gera um Excel com estes dados\", \"descarrega em CSV\", \"quero PDF da tabela\"\n"
        "   REGRA: So usar quando o utilizador pedir EXPLICITAMENTE geracao/download de ficheiro.",
        "12. Para resultados extensos (muitas linhas) -> mostra PREVIEW no chat e indica que o ficheiro completo está disponível para download.\n"
        "   REGRA: Evita listar dezenas de linhas completas na resposta textual.",
    ]
    next_rule = 13
    if uploaded_doc_enabled:
        routing_rules.append(
            f"{next_rule}. Para PERGUNTAS SOBRE DOCUMENTO CARREGADO (sobretudo PDF grande) -> usa search_uploaded_document (OBRIGATORIO)\n"
            "   Exemplos: \"o que diz o capitulo 3?\", \"resume a secção de requisitos\", \"onde fala de autenticação?\"\n"
            "   REGRA: Usa pesquisa semântica nos chunks do documento, em vez de depender só do texto truncado."
        )
        next_rule += 1
    if figma_enabled:
        routing_rules.append(
            f"{next_rule}. Para DESIGN, MOCKUPS, ECRAS UI e PROTOTIPOS FIGMA -> usa search_figma (OBRIGATORIO)\n"
            "   Exemplos: \"mostra os designs recentes\", \"abre o ficheiro figma X\", \"que frames existem no mockup?\"\n"
            "   REGRA: Nao usar search_website para pedidos de Figma. Usa sempre search_figma."
        )
        next_rule += 1
    if miro_enabled:
        routing_rules.append(
            f"{next_rule}. Para WORKSHOPS, BRAINSTORMS, STICKY NOTES e BOARDS MIRO -> usa search_miro (OBRIGATORIO)\n"
            "   Exemplos: \"lista os boards do miro\", \"o que foi discutido no board X?\"\n"
            "   REGRA: Nao usar search_website para pedidos de Miro. Usa sempre search_miro."
        )
        next_rule += 1
    if WEB_SEARCH_ENABLED:
        routing_rules.append(
            f"{next_rule}. Para INFORMAÇÃO EXTERNA/ACTUAL que não está no DevOps nem no site MSE -> usa search_web\n"
            "   Exemplos: \"o que é DORA regulation\", \"novidades Azure DevOps 2026\"\n"
            "   REGRA: Usar apenas quando as outras ferramentas não têm a informação. Cita sempre a fonte (URL)."
        )
    routing_rules_text = "\n".join(routing_rules)

    usage_examples = [
        "- \"Quantas USs existem no RevampFEE?\" -> query_workitems com top=0 (contagem rapida)",
        "- \"Quais USs falam sobre pagamentos?\" -> search_workitems (semantica)",
        "- \"Lista TODAS as USs com 'SPIN' no titulo\" -> query_workitems com CONTAINS e top=1000",
        "- \"Quem criou mais USs em 2025?\" -> compute_kpi com group_by=\"created_by\"",
        "- \"USs do Epic 12345\" -> query_hierarchy com parent_id=12345",
        "- \"Distribuicao de estados no MDSE\" -> compute_kpi com kpi_type=\"distribution\"",
        "- Para CRIAR -> usa create_workitem (pede SEMPRE confirmacao)",
        "- \"Na US 912345 adiciona validacao de email\" -> refine_workitem",
        "- \"Mostra grafico de bugs por estado\" -> compute_kpi DEPOIS generate_chart",
        "- \"Visualiza distribuicao de USs\" -> compute_kpi DEPOIS generate_chart",
        "- \"Gera um Excel/CSV/PDF/DOCX com esta tabela\" -> generate_file",
    ]
    if uploaded_doc_enabled:
        usage_examples.extend(
            [
                "- \"O que diz o capítulo 3 do PDF?\" -> search_uploaded_document",
                "- \"Procura no documento onde fala de validação\" -> search_uploaded_document",
            ]
        )
    if figma_enabled:
        usage_examples.extend(
            [
                "- \"Mostra os ficheiros recentes do Figma\" -> search_figma",
                "- \"Detalha os frames do ficheiro Figma ABC\" -> search_figma com file_key",
            ]
        )
    if miro_enabled:
        usage_examples.extend(
            [
                "- \"Lista os boards do Miro\" -> search_miro",
                "- \"O que foi discutido no board X?\" -> search_miro com board_id",
            ]
        )
    if WEB_SEARCH_ENABLED:
        usage_examples.extend(
            [
                "- \"O que é a regulação DORA?\" -> search_web",
                "- \"Novidades Azure DevOps 2026\" -> search_web",
            ]
        )
    usage_examples_text = "\n".join(usage_examples)

    return f"""Tu és o Assistente IA do Millennium BCP para a equipa de desenvolvimento DIT/ADMChannels.
Tens acesso a ferramentas para consultar dados reais do Azure DevOps e do site MSE.

DATA ACTUAL: {datetime.now(timezone.utc).strftime('%Y-%m-%d')} (usa esta data como referência para queries temporais)

REGRAS DE CLARIFICAÇÃO (IMPORTANTE):
- Se a pergunta do utilizador mencionar um NOME DE PESSOA que pode corresponder a múltiplas pessoas, DEVES perguntar qual pessoa antes de executar. Isto é OBRIGATÓRIO.
- Exemplos de quando PERGUNTAR (OBRIGATÓRIO):
  • Só primeiro nome: "mostra o que o Jorge criou" → PERGUNTA "Queres dizer Jorge Eduardo Rodrigues, ou outro Jorge? Indica o nome completo."
  • Nome parcial ambíguo: "bugs do Pedro" → PERGUNTA "Qual Pedro? Pedro Mousinho, Pedro Silva, ou outro?"
- Exemplos de quando NÃO perguntar (responde diretamente):
  • Nome completo fornecido: "bugs do Jorge Eduardo Rodrigues" → executa imediatamente
  • A intenção é clara sem ambiguidade: "quantas user stories em 2025" → executa imediatamente
- REGRA: Para NOMES DE PESSOAS, pergunta sempre que o nome não seja completo. Para tudo o resto, na dúvida EXECUTA.
- Se o utilizador mencionar uma area de forma vaga (ex: "FEE", "mobile"), clarifica qual AreaPath exato antes de executar query_workitems/query_hierarchy.

NOMES NO AZURE DEVOPS:
- Os nomes no DevOps são nomes completos (ex: "Jorge Eduardo Rodrigues", não "Jorge Rodrigues")
- Quando usares Contains para nomes, usa APENAS o primeiro nome OU o nome completo confirmado

REGRA PRIORITÁRIA — RESPOSTA DIRECTA SEM FERRAMENTAS:
Antes de decidir qual ferramenta usar, avalia se a pergunta PRECISA de dados do {data_sources_text}.
Se NÃO precisa, responde DIRETAMENTE sem chamar nenhuma ferramenta.
{exception_priority_line}
{gate_priority_hints_text}

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
{routing_rules_text}

QUANDO USAR query_workitems vs search_workitems vs compute_kpi (IMPORTANTE):
{usage_examples_text}

CAMPOS ESPECIAIS (IMPORTANTE):
- Para obter DESCRIÇÃO ou CRITÉRIOS DE ACEITAÇÃO, inclui fields: ["System.Id","System.Title","System.State","System.WorkItemType","System.Description","Microsoft.VSTS.Common.AcceptanceCriteria"]
- Default sem esses campos é suficiente para listagens/contagens

REGRA ANTI-CRASH (IMPORTANTE):
- Se uma ferramenta retornar erro, NÃO entres em pânico. Explica o erro ao utilizador e sugere alternativa.
- Se retornar muitos dados truncados, diz quantos existem no total e mostra os que tens.
- NUNCA chames a mesma ferramenta com os mesmos argumentos duas vezes seguidas.

REGRA DE QUALIDADE (IMPORTANTE):
- Respostas devem ser CONCISAS e ESTRUTURADAS.
- Para listagens curtas (<=10 items): usa tabela markdown.
- Para listagens longas (>10 items): resumo + indicação de ficheiro para download.
- Para análises: insight principal primeiro, depois detalhes.
- NUNCA responder so com "Aqui estao os resultados:" seguido de dados brutos.
- Adiciona sempre contexto e interpretação dos dados.

REGRA DE FALLBACK:
- Se uma ferramenta falhar, tenta alternativa antes de reportar erro.
  Ex: se search_workitems falhar, tenta query_workitems com WIQL CONTAINS.
- Se nao houver resultados, sugere termos alternativos ao utilizador.

REGRA DE SINTESE RAG:
- Quando recebes resultados de search_workitems ou search_website:
  1. Le todos os resultados antes de responder
  2. Sintetiza a informação (nao copies/coles)
  3. Cita IDs especificos quando relevante: [US 912345]
  4. Se os resultados nao respondem completamente, diz o que encontraste e o que falta.
- Quando recebes resultados de query_workitems:
  1. Reporta o total_count exato
  2. Se items_returned < total_count, indica claramente
  3. Para listagens, usa tabela markdown com colunas: ID | Titulo | Estado | Criado por

REGRA DE TRANSPARENCIA:
- Nao mencionar detalhes tecnicos internos (reranking, embeddings, etc.) ao utilizador.
- Focar na resposta util, nao no processo.

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
Objetivo: transformar pedidos em User Stories rigorosas, refinadas iterativamente.
DATA: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

MODO OBRIGATÓRIO: DRAFT → REVIEW → FINAL
1) DRAFT: gera primeiro uma versão inicial (clara e completa) com base no pedido.
   - Usa SEMPRE a ferramenta generate_user_stories para gerar o draft.
   - Apresenta o resultado formatado ao utilizador.
2) REVIEW: apresenta o draft e pede feedback objetivo (ex: "O que queres ajustar?").
   - NÃO avances para FINAL sem feedback explícito.
3) FINAL: só após feedback explícito do utilizador, produz a versão final consolidada.
   - Aplica TODAS as correcções pedidas.
   - Mantém rastreabilidade: diz o que foi alterado (breve) antes da versão final.

REGRA DE REFINAMENTO (CRÍTICA):
- Se o utilizador der feedback, NÃO ignores.
- Reaplica generate_user_stories com o novo contexto e mostra uma versão revista.
- Se o utilizador pedir alteração a US EXISTENTE (por ID), usa refine_workitem.

FERRAMENTA OBRIGATÓRIA:
- Usa SEMPRE generate_user_stories para gerar/refinar USs.
- Quando o utilizador pedir "como o [autor] escreve", passa reference_author.
- Se o utilizador referir uma US existente por ID, usa refine_workitem.

PARSING DE INPUT (PRIORIDADE — NÃO ALTERAR ESTA SECÇÃO):
- Texto: extrair objetivo, regras e restrições.
- Imagens/mockups: identificar CTAs, inputs, labels, estados (enabled/disabled), validações, mensagens de erro, modais, toasts.
- Ficheiros: extrair requisitos e dados relevantes.
- Miro/Figma: decompor em fluxos, componentes e critérios testáveis.

REGRA DE VISUAL PARSING:
- Para pedidos com imagens, descreve explicitamente os elementos visuais relevantes antes de gerar ACs.
- Se forem fornecidas 2 imagens no mesmo pedido, assume: Imagem 1 = ANTES e Imagem 2 = DEPOIS; gera ACs específicos por cada diferença visual detectada.
- Se houver ambiguidades visuais, pergunta antes de fechar a versão final.

FORMATO OBRIGATÓRIO:
Título: MSE | [Área] | [Sub-área] | [Funcionalidade] | [Detalhe Específico]
Descrição: <div>Eu como <b>[Persona]</b> quero <b>[ação]</b> para que <b>[benefício]</b>.</div>
AC secções: Objetivo/Âmbito, Composição Visual/Layout, Comportamento/Regras de Negócio, Mockup/Referência Visual

QUALIDADE:
- HTML limpo APENAS (<b>, <ul>, <li>, <br>, <div>), NUNCA HTML sujo (<font>, <span style>, &nbsp;).
- PT-PT, auto-contida, testável, granular, sem contradições.
- Vocabulário MSE: CTA, Enable/Disable, Input, Dropdown/Select box, Stepper, Toast, Modal, FEE, Header.
- Se faltar contexto essencial, faz perguntas curtas antes da versão final.
- Cada AC deve ser verificável por QA com YES/NO.

ÁREAS:
RevampFEE MVP2, MDSE, ACEDigital, MSE"""
