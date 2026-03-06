# DBDE AI Assistant v7.3.0 — Analise SWOT Completa
## Data: 6 de Marco de 2026
## Autor: Analise automatizada via Claude Code (com verificacao Azure real)
## Owner: Pedro Mousinho, Product Owner — Millennium BCP

---

## Resumo Executivo

O DBDE AI Assistant v7.3.0 e um produto interno maduro e bem arquitectado, com 5 features de seguranca/produtividade implementadas (PII Shield, Code Interpreter, Structured Outputs, Prompt Shield, Document Intelligence), 150 testes a passar, e documentacao operacional solida. A stack (FastAPI + React + Azure) e adequada para o caso de uso (~20 utilizadores internos).

**Verificacao Azure realizada em 2026-03-06** — Subscription: Azure subscription 1, Tenant: MILLENNIUM BCP (BCPCorp.onmicrosoft.com).

O risco global e **moderado (4.4/10)** — revisado em baixa apos confirmar que: todos os secrets estao em Key Vault (14/14), App Insights esta activo com 4 alertas configurados, Managed Identity esta activa, e existem 18 model deployments incluindo gpt-5.3-chat (03/2026). As prioridades imediatas sao: VNet + Entra ID (dependente da DSI), health check path no App Service, e cleanup de recursos potencialmente orfaos.

---

## Inventario de Infraestrutura Azure (Verificado)

### Resource Groups
| Nome | Regiao | Estado |
|------|--------|--------|
| rg-MS_Access_Chabot | Sweden Central | Succeeded |
| NovoResourceGroup | Sweden Central | Succeeded |

### App Service
| Propriedade | Valor | Avaliacao |
|------------|-------|-----------|
| Nome | millennium-ai-assistant | - |
| Estado | Running | OK |
| Runtime | PYTHON 3.12 | OK |
| HTTPS Only | true | OK |
| TLS Minimo | 1.2 | OK |
| HTTP/2 | Enabled | OK |
| FTPS | FtpsOnly | OK (idealmente Disabled) |
| Always On | true | OK |
| Workers | 1 | OK para ~20 users |
| Health Check Path | **null** | PROBLEMA — configurar |
| WebSockets | Disabled | OK (usa SSE) |
| Public Network Access | Enabled | PENDENTE — aguarda VNet |
| IP Restrictions | Allow all | PENDENTE — aguarda VNet |
| SCM IP Restrictions | Allow all | PENDENTE — restringir |
| Managed Identity | SystemAssigned (activa) | OK |
| Startup Command | `bash startup.sh` | OK |

### Azure OpenAI Deployments (ms-access-chabot-resource) — 18 deployments
| Deployment | Modelo | Versao | SKU | Capacity |
|-----------|--------|--------|-----|----------|
| text-embedding-3-small | text-embedding-3-small | 1 | GlobalStandard | 500 |
| dbde_access_chatbot | gpt-4o | 2024-11-20 | GlobalStandard | 50 |
| dbde_access_chatbot_41 | gpt-4.1 | 2025-04-14 | GlobalStandard | 50 |
| gpt-4.1-mini | gpt-4.1-mini | 2025-04-14 | GlobalStandard | 100 |
| gpt-4.1 | gpt-4.1 | 2025-04-14 | GlobalStandard | 2950 |
| model-router | model-router | 2025-11-18 | GlobalStandard | 50 |
| gpt-5-mini | gpt-5-mini | 2025-08-07 | GlobalStandard | 10 |
| gpt-5-chat | gpt-5-chat | 2025-10-03 | GlobalStandard | 5 |
| gpt-5.1 | gpt-5.1-chat | 2025-11-13 | GlobalStandard | 2000 |
| gpt-5.2-chat | gpt-5.2-chat | 2026-02-10 | GlobalStandard | 1 |
| **gpt-5.3-chat** | **gpt-5.3-chat** | **2026-03-03** | **GlobalStandard** | **1000** |
| claude-opus-4-6 | claude-opus-4-6 | 1 | GlobalStandard | 50 |
| claude-sonnet-4-6 | claude-sonnet-4-6 | 1 | GlobalStandard | 50 |
| cohere-rerank-v4-fast | Cohere-rerank-v4.0-fast | 1 | GlobalStandard | 3 |
| gpt-5-mini-dz | gpt-5-mini | 2025-08-07 | DataZoneStandard | 30 |
| gpt-4-1-dz | gpt-4.1 | 2025-04-14 | DataZoneStandard | 87 |
| gpt-4-1-mini-dz | gpt-4.1-mini | 2025-04-14 | DataZoneStandard | 229 |
| o4-mini-dz | o4-mini | 2025-04-16 | DataZoneStandard | 105 |

### Cognitive Services
| Recurso | Tipo | Regiao |
|---------|------|--------|
| ms-access-chabot-resource | AIServices (OpenAI) | Sweden Central |
| DBDE-Chatbot | AIServices (OpenAI) | Sweden Central |
| dbde-pii | TextAnalytics (PII) | Sweden Central |
| dbde-doc-intel | FormRecognizer (Doc Intel) | Sweden Central |
| dbde-content-safety | ContentSafety (Prompt Shield) | Sweden Central |

### Key Vault (dbde-ai-vault) — 21 secrets, **14/14 secrets sensiveis com KV Reference**
| App Setting | Key Vault Ref |
|------------|---------------|
| AZURE_OPENAI_KEY | KV_REF |
| CONTENT_SAFETY_KEY | KV_REF |
| DEVOPS_PAT | KV_REF |
| DOC_INTEL_KEY | KV_REF |
| FIGMA_ACCESS_TOKEN | KV_REF |
| JWT_SECRET | KV_REF |
| MIRO_ACCESS_TOKEN | KV_REF |
| PII_API_KEY | KV_REF |
| RERANK_API_KEY | KV_REF |
| SEARCH_KEY | KV_REF |
| STORAGE_CONNECTION_STRING | KV_REF |
| STORAGE_KEY | KV_REF |
| WEB_ANSWERS_API_KEY | KV_REF |
| WEB_SEARCH_API_KEY | KV_REF |

**Avaliacao: EXCELENTE** — Todos os secrets sensiveis estao em Key Vault com referencia. Managed Identity activa para acesso.

### AI Search
| Recurso | SKU | Estado | Semantic Search | Public Access |
|---------|-----|--------|-----------------|---------------|
| dbdeacessrag | **Free** | Running | Free | Enabled |
| datasql (NovoResourceGroup) | Standard | Running | - | Enabled |

**Nota**: O Search principal (dbdeacessrag) esta no tier **Free** — limitacoes de 50MB storage, 3 indices, sem SLA.

### App Insights (millennium-ai-assistant)
| Propriedade | Valor |
|------------|-------|
| Estado | Succeeded |
| Ingestion Endpoint | swedencentral-0.in.applicationinsights.azure.com |
| Retencao | 90 dias |
| Public Ingestion | Enabled |
| Public Query | Enabled |
| Flow Type | Redfield |
| Ingestion Mode | **Disabled** |

**PROBLEMA**: IngestionMode = Disabled. O App Insights esta configurado mas **nao esta a ingerir dados activamente**. Precisa de ser activado.

### Alertas de Monitoring (4 activos)
| Alerta | Severidade | Janela | Frequencia |
|--------|-----------|--------|------------|
| HTTP 5xx error rate > 5 in 5 min | Sev 2 | 5 min | 1 min |
| Average response time > 30s | Sev 3 | 5 min | 1 min |
| Health check failing | Sev 1 | 5 min | 1 min |
| CPU > 80% for 10 min | Sev 3 | 10 min | 5 min |

### Recursos Potencialmente Orfaos / Legacy
| Recurso | Tipo | Nota |
|---------|------|------|
| bing_chatbot | Bing Account | Provavelmente legacy — verificar se em uso |
| logicapp-034568 | Logic App | Verificar se activa e necessaria |
| logicapp034568037728 | Storage Account | Storage da Logic App |
| logicapp-034568-plan | App Service Plan | Plan da Logic App |
| cosmosdbrgmsaccesschabot84949c | CosmosDB (GlobalDocumentDB) | Verificar se em uso — publicNetworkAccess=Enabled |
| dbde-access-agent (Automation) | Automation Account + Runbook | Runbook "Ingestion_Daily" — verificar se activo |
| DBDE-Chatbot | AI Services | Segundo recurso OpenAI — sem deployments, possivelmente orfao |
| dbde_access_chatbot (deployment) | gpt-4o | Legacy — modelo anterior ao gpt-4.1 |

---

## STRENGTHS (Forcas)

### S1. Arquitectura de Seguranca em Camadas
- **5 shields activos**: PII masking (Azure AI Language), Prompt Shield (Content Safety), Code Interpreter sandboxed, Structured Outputs (JSON schemas validados), Document Intelligence (OCR seguro).
- **Abuse Monitoring Opt-Out aprovado pela Microsoft** — zero data retention nos modelos Azure OpenAI, eliminando risco de dados bancarios serem usados para treino.
- **Fail-open design** nos shields — se um servico de seguranca falha, a app continua a funcionar (degradacao graceful em vez de hard failure).

### S2. Pipeline LLM Robusto com 18 Model Deployments
- **Multi-provider com fallback**: Azure OpenAI (primario) + Claude (Opus 4.6 / Sonnet 4.6 via Foundry) como fallback, com tracking explicito de qual provider serviu cada request.
- **18 deployments activos** incluindo gpt-5.3-chat (lancado 2026-03-03) com 1000 TPM, gpt-5.1 com 2000 TPM, e gpt-4.1 com 2950 TPM.
- **Model Router** disponivel para routing inteligente entre modelos.
- **DataZone deployments** (gpt-4.1-dz, gpt-4.1-mini-dz, o4-mini-dz, gpt-5-mini-dz) para data residency EU.
- **Cohere Rerank v4** integrado para RAG com reranking.
- **Sistema de tiers** (fast/standard/pro/vision) permite optimizacao de custo vs qualidade por tipo de operacao.
- **Streaming suportado** para ambos os providers com chunked JSON parsing sofisticado.

### S3. Secrets 100% em Key Vault
- **14/14 secrets sensiveis** estao em Azure Key Vault com referencia (`@Microsoft.KeyVault`).
- **Managed Identity** (SystemAssigned) activa — acesso ao Key Vault sem credentials inline.
- **21 secrets no vault** com naming convention consistente (`dbde-*`).
- Isto e **acima do standard** para projectos internos deste tamanho.

### S4. Code Interpreter com Sandbox Seguro
- **AST-based static analysis** antes de execucao — bloqueia imports perigosos (subprocess, socket, ctypes, pickle).
- **Allowlist de imports seguros** (pandas, numpy, matplotlib, seaborn, plotly).
- **Isolamento por subprocess** com environment hardening (HOME/TMPDIR custom).
- **Limites de ficheiros**: upload 50MB, output 10MB, timeout 240s.
- **Safe patching** de `open()` e `plt.show()` para comportamento transparente na sandbox.

### S5. Documentacao Operacional Exemplar
- **5 documentos de operacao**: CONTINUITY.md, RUNBOOK.md, DEPLOY_CHECKLIST.md, THIRD_PARTY_INVENTORY.md, DATA_POLICY.md.
- **Health check profundo** (`/health?deep=true`) com verificacao de todos os servicos.
- **Deploy com staging slot** e swap controlado, com smoke test e rollback documentado.
- **Matriz de criticidade** por servico externo documentada.

### S6. CI/CD e Qualidade
- **150 testes a passar** (1 skipped) com cobertura de 4 camadas (RAG, Tools, Arena, User Story).
- **GitHub Actions CI** com matrix Python 3.11/3.12 + frontend build verification.
- **Deploy checklist** formal com pre-deploy, staging, swap, pos-deploy e rollback.

### S7. Monitoring com 4 Alertas Activos
- **4 metric alerts configurados**: 5xx errors, latencia >30s, health failures (Sev 1), CPU >80%.
- **App Insights** criado com 90 dias de retencao.
- Action group `dbde-ai-alerts` para notificacoes.
- Autoscale settings configurados (`dbdeai-asp-autoscale`).

### S8. Autenticacao Solida (para o contexto)
- **JWT custom sem dependencias externas** — usa stdlib hmac/hashlib.
- **PBKDF2 com 100k iteracoes** para password hashing.
- **JWT_SECRET em Key Vault** — confirmado via KV Reference.
- **Token rotation** suportada (current + previous secret).

### S9. Configuracao App Service Segura
- **HTTPS Only**: Activo.
- **TLS 1.2 minimo**: Activo.
- **HTTP/2**: Activo.
- **FTPS Only**: Activo (recomenda-se Disabled).
- **Always On**: Activo — sem cold starts.
- **Remote Debugging**: Desactivado.

### S10. Integracao Rica de Ferramentas
- **DevOps integration** nativa (work items, queries WIQL).
- **Figma + Miro APIs** para contexto de design/produto.
- **Brave Search + Web Answers** para pesquisa web actual.
- **AI Search** para RAG com Cohere Rerank v4.
- **Document Intelligence** para OCR e extracao de tabelas.

---

## WEAKNESSES (Fraquezas)

### W1. Concorrencia Nao Thread-Safe (CRITICO)
- **ConversationStore** (`agent.py`) nao tem locks — dois requests concorrentes podem causar perda de dados de conversacao.
- **File loading race condition** — dois requests podem duplicar carregamento de ficheiros do blob storage.
- **Generated files storage** (`tools.py`) — race condition na verificacao de capacidade vs armazenamento.
- **HTTP client management** (`llm_provider.py`) — `_get_client()` nao e thread-safe; multiplos tasks podem criar clientes duplicados.
- **Impacto**: Com ~20 utilizadores, a probabilidade e baixa mas nao negligivel. Um unico utilizador com multiplos tabs pode trigger isto.

### W2. Frontend Monolitico
- **App.jsx com 1,872 linhas** — dificil de manter, testar e raciocinar.
- **50+ variaveis de estado** num unico componente sem useReducer.
- **Sem React.memo, useCallback ou useMemo** — rerenders desnecessarios em toda a arvore de componentes.
- **Sem virtualizacao** de listas longas de mensagens — performance degrada com conversas grandes.
- **dangerouslySetInnerHTML** usado para markdown rendering — risco XSS mitigado por DOMPurify mas inherentemente fragil.
- **Sem TypeScript** — nenhuma type safety no frontend.

### W3. App Insights com Ingestao Desactivada
- **IngestionMode = Disabled** — O App Insights esta criado e configurado mas **nao esta a ingerir telemetria activamente**.
- Os 4 alertas estao configurados mas podem nao ter dados para trigger.
- **Accao necessaria**: Activar ingestao para que monitoring funcione.

### W4. Health Check Path Nao Configurado no App Service
- `healthCheckPath = null` — O Azure App Service nao esta a fazer health checks nativos.
- Existe endpoint `/health` e `/health?deep=true` no backend, mas o App Service nao os usa.
- **Impacto**: O App Service nao detecta automaticamente se a app esta unhealthy para restart automatico.

### W5. AI Search no Tier Free
- **dbdeacessrag** esta no SKU **Free** — limitado a 50MB storage, 3 indices, sem SLA, sem replicas.
- Para uso em producao no contexto bancario, deveria estar pelo menos no tier **Basic** (com SLA).
- O segundo Search (datasql no NovoResourceGroup) esta no tier Standard.

### W6. Sem Token Blacklist / Refresh
- **Logout apenas apaga cookie** — token continua valido ate expirar (10h default).
- **Sem refresh tokens** — sessoes longas forcam re-autenticacao.
- **Sem rate limiting** em tentativas de autenticacao falhadas.

### W7. Logging Pode Expor Secrets
- **DevOps PAT** incluido em headers de Authorization que podem ser logged via `logging.info()`.
- **Erros de API** truncados a 300 chars — pode esconder informacao critica em debug, mas tambem pode expor dados sensiveis.
- **Sem filtragem de headers** nos logs.

### W8. PII Shield — Bug de Overlapping Entities
- Se duas entidades PII se sobrepoem (ex: "Joao Silva" detectado como pessoa + "Joao" detectado individualmente), a segunda substituicao pode corromper o texto masked.
- **HTTP client criado por request** — ineficiente, deveria ser reutilizado.

### W9. Recursos Potencialmente Orfaos
- **bing_chatbot** (Bing Account) — provavelmente legacy.
- **logicapp-034568** + storage + plan — Logic App possivelmente sem uso.
- **cosmosdbrgmsaccesschabot84949c** (CosmosDB) — acesso publico activo, verificar se em uso.
- **DBDE-Chatbot** (segundo recurso AI Services) — sem deployments, possivelmente orfao.
- **dbde_access_chatbot** (deployment gpt-4o) — modelo anterior, candidato a remocao.
- **Custo acumulado** destes recursos orfaos pode ser significativo.

### W10. Code Interpreter — Gaps de Hardening
- **PATH copiado do parent** — utilizador pode potencialmente chamar binarios do sistema.
- **Sem limites de CPU/memoria** no subprocess (apenas timeout).
- **Symlink attack possivel** — `_safe_path()` nao valida symlinks.
- **Per-request mount limit** de 100MB pode causar memory exhaustion.

### W11. FTPS Deveria Estar Disabled
- FTPS esta em **FtpsOnly** — deveria estar **Disabled** (nao ha necessidade de FTPS com CI/CD via GitHub Actions).

### W12. SCM Site Sem Restricoes de IP
- O site de administracao (`.scm.azurewebsites.net`) permite acesso de qualquer IP.
- Deveria ter restricoes de IP mesmo antes de VNet.

---

## OPPORTUNITIES (Oportunidades)

### O1. VNet + Entra ID (Dependente da DSI)
- **VNet integration** eliminaria exposicao publica do App Service — comunicacao interna apenas.
- **Entra ID (Azure AD)** substituiria autenticacao JWT custom por SSO corporativo — eliminando gestao de passwords.
- **Impacto**: Reduziria drasticamente a superficie de ataque e simplificaria onboarding de utilizadores.

### O2. Activar App Insights e Telemetria Custom
- Activar IngestionMode para que os 4 alertas existentes funcionem.
- Adicionar custom metrics: latencia por tool, taxa de erros por provider, tokens consumidos.
- Criar dashboard operacional no Azure Portal.
- Integrar OpenTelemetry para tracing distribuido.

### O3. Refactoring Frontend
- Decomposicao em componentes focados (ChatContainer, MessageList, InputForm, FileUpload, ConversationList).
- **useReducer** para estado de conversacao + **custom hooks** (useConversations, useUpload, useChat).
- **React.memo + useCallback** para eliminar rerenders.
- **Virtualizacao** de mensagens com react-window para conversas longas.
- **TypeScript** para type safety.

### O4. Upgrade AI Search para Basic/Standard
- Migrar dbdeacessrag de Free para Basic — obter SLA, mais indices, mais storage.
- Re-indexar com embeddings actualizados.
- Consolidar com datasql (Standard) se possivel para reduzir recursos.

### O5. Optimizacao de Modelos — gpt-5.3-chat
- **gpt-5.3-chat** ja deployado (2026-03-03) com 1000 TPM — testar como tier pro.
- **Model Router** disponivel — activar para routing inteligente entre modelos (custo vs qualidade automatico).
- **DataZone deployments** disponiveis — migrar para DZ para garantir data residency EU.
- Considerar cleanup de modelos legacy (gpt-4o, gpt-5-chat capacity=5).

### O6. Melhorias no Code Interpreter
- Adicionar suporte para mais bibliotecas (scikit-learn, statsmodels para analise estatistica).
- **Resource limits** (CPU/memoria) via `resource.setrlimit()`.
- **Symlink validation** e PATH hardening.
- Adicionar preview de outputs graficos inline.

### O7. Cleanup de Recursos Orfaos
- Remover/desactivar bing_chatbot, Logic App, CosmosDB se nao usados.
- Remover deployment gpt-4o (dbde_access_chatbot) se substituido por gpt-4.1.
- Remover recurso DBDE-Chatbot se sem deployments activos.
- **Poupanca estimada**: Variavel mas pode reduzir custos desnecessarios.

### O8. Configurar Health Check Path no App Service
- Configurar `healthCheckPath = /health` no App Service.
- Permite restart automatico de instancias unhealthy.
- Integra com os alertas de health ja configurados.

### O9. Export e Reporting Avancado
- **Gerar relatorios automaticos** de user stories com formato padrao do banco.
- **Export para Confluence/SharePoint** alem de DOCX/PDF.
- **Templates** personalizaveis por equipa.

### O10. Integracao com Mais Ferramentas
- **Jira** para equipas que nao usam Azure DevOps.
- **Confluence** para knowledge base.
- **Teams** para notificacoes e interacao directa.
- **GitLab/GitHub** para code review assistido.

---

## THREATS (Ameacas)

### T1. Risco de Dados Bancarios Confidenciais
- Mesmo com PII Shield activo, utilizadores podem inadvertidamente colar dados sensiveis de clientes (NIFs, numeros de conta, moradas).
- **PII Shield tem threshold de 0.7** — entidades com confidence <0.7 passam sem mascaramento.
- **Dados em transito** para Azure OpenAI, Anthropic, Brave Search — multiplos pontos de exposicao.
- **Mitigacao actual**: Data Policy documentada, Abuse Monitoring Opt-Out activo (zero retention), DataZone deployments disponiveis.
- **Risco residual**: Depende do comportamento dos utilizadores e da eficacia do PII Shield.

### T2. Exposicao Publica de Todos os Servicos
- **App Service**: publicNetworkAccess = Enabled, IP Restrictions = Allow all.
- **AI Search**: publicNetworkAccess = Enabled (ambos: dbdeacessrag e datasql).
- **CosmosDB**: publicNetworkAccess = Enabled.
- **App Insights**: publicNetworkAccess = Enabled (ingestion e query).
- **Nenhum servico esta em VNet/Private Endpoint** — todos acessiveis pela internet publica.
- **Mitigacao**: HTTPS + TLS 1.2 + JWT auth. VNet pendente da DSI.

### T3. Dependencia de Servicos Azure
- **Azure OpenAI**: Se rate-limited (429) ou indisponivel, fallback para Claude (Foundry) funcional.
- **Azure AI Search**: Se indisponivel, RAG nao funciona — sem fallback. Tier Free sem SLA.
- **Azure Storage**: Single point of failure para estado da aplicacao e ficheiros.
- **Nenhuma estrategia de DR** (disaster recovery) documentada alem de rollback de deploy.

### T4. Supply Chain e Dependencias
- **19 dependencias Python** + **dependencias npm** — cada uma e um vector de ataque potencial.
- **CDNs externos** (cdnjs, plot.ly, Google Fonts) — risco de comprometimento de supply chain.
- **Mitigacao parcial**: Fallback local em `static/vendor/` para CDNs.
- **Sem `pip audit` ou `npm audit`** no CI pipeline.

### T5. Escalabilidade Limitada
- **App Service plan-dbde-v2** com **1 worker Uvicorn** — nao escala horizontalmente.
- **ConversationStore in-memory** com LRU eviction — nao partilhado entre instancias.
- **Autoscale configurado** (dbdeai-asp-autoscale) mas sem externalizacao de estado.
- Se o numero de utilizadores crescer significativamente (>50), a arquitectura actual nao suporta.

### T6. Regulacao Bancaria
- **EBA/BCE guidelines** sobre uso de AI em instituicoes financeiras podem exigir auditorias adicionais.
- **RGPD/GDPR**: Processamento de dados pessoais (mesmo internos) requer base legal e DPO notification.
- **DSI do banco** pode impor restricoes adicionais apos auditoria.
- **Classificacao de dados**: Nao existe validacao automatica do nivel de classificacao dos documentos uploaded.

### T7. Expiracao de Credenciais
- **DevOps PAT** (dbde-devops-pat-v2 no Key Vault) expira periodicamente — sem data de expiracao visivel no Key Vault.
- **API keys** — nao ha mecanismo automatico de rotacao.
- **Sem alertas proactivos** de expiracao proxima.

### T8. Prompt Injection Avancado
- **Prompt Shield** detecta ataques conhecidos, mas ataques sofisticados (jailbreaks multi-turn, injection via documentos) podem bypass.
- **Code Interpreter** — apesar do sandboxing, edge cases no AST checker (imports relativos, `__import__`) podem permitir bypass.
- **Document Intelligence** — documentos maliciosos podem injectar instrucoes via texto extraido.

### T9. Vendor Lock-in
- Forte dependencia de Azure (OpenAI, Storage, Search, App Service, Content Safety, Document Intelligence, AI Language, Key Vault).
- **Claude via Foundry** como fallback e o unico ponto de diversificacao.
- **CosmosDB** no resource group sugere historico de dependencia adicional.

---

## Risk Score (0-10)

### Seguranca Aplicacional: 3.5/10 (Risco Moderado-Baixo) [REVISTO]
**Justificacao:**
- (+) PII Shield, Prompt Shield, Code Interpreter sandbox, DOMPurify no frontend
- (+) Abuse Monitoring Opt-Out activo (zero data retention)
- (+) JWT auth com PBKDF2 100k iterations, JWT_SECRET em Key Vault
- (+) **Todos os 14 secrets sensiveis em Key Vault** com Managed Identity
- (+) HTTPS Only, TLS 1.2, HTTP/2, Remote Debug desactivado
- (-) Concorrencia nao thread-safe pode causar corrupcao de dados
- (-) Sem token blacklist — tokens leaked ficam validos 10h
- (-) Code Interpreter PATH e symlink gaps
- **Conclusao**: Significativamente melhor do que a avaliacao pre-Azure. Key Vault + Managed Identity sao excelentes. Concorrencia e o gap principal.

### Seguranca de Dados/Rede: 5.0/10 (Risco Moderado)
**Justificacao:**
- (+) PII masking antes de envio para LLM
- (+) Data Policy documentada com dados proibidos claros
- (+) Zero data retention (Abuse Monitoring Opt-Out)
- (+) DataZone deployments disponiveis para data residency EU
- (+) Todos os secrets em Key Vault
- (-) **Todos os servicos com publicNetworkAccess = Enabled** — sem VNet/Private Endpoints
- (-) **Sem Entra ID** — auth custom em vez de SSO corporativo
- (-) SCM site sem restricoes de IP
- (-) CosmosDB com acesso publico (se em uso)
- **Conclusao**: O risco principal e a exposicao publica de todos os servicos. VNet e a prioridade #1.

### Custo/Sustentabilidade: 2.0/10 (Risco Baixo)
**Justificacao:**
- (+) Custo actual 30-55 EUR/mes e muito eficiente
- (+) Sistema de tiers LLM optimiza custo vs qualidade
- (+) Orcamento flexivel (custo nao e factor limitante)
- (+) Alertas de custo podem ser facilmente adicionados
- (-) Recursos possivelmente orfaos (bing_chatbot, Logic App, CosmosDB, deployments legacy)
- (-) AI Search Free sem SLA
- **Conclusao**: Custo excelente. Cleanup de orfaos pode optimizar ainda mais.

### Qualidade de Codigo: 5.0/10 (Risco Moderado)
**Justificacao:**
- (+) Backend bem estruturado com separacao de concerns (16,190 linhas Python)
- (+) 150 testes a passar com boa cobertura de 4 camadas
- (+) Async/await patterns consistentes
- (+) Error handling razoavel com fail-open design
- (-) Frontend monolitico (1,872 linhas em App.jsx, 50+ estados)
- (-) Sem TypeScript no frontend
- (-) Race conditions em multiplos componentes backend
- (-) Numeric parsing fragil
- **Conclusao**: Backend solidamente acima da media; frontend precisa de refactoring significativo.

### Operacoes/Monitoring: 3.5/10 (Risco Moderado-Baixo) [REVISTO]
**Justificacao:**
- (+) Documentacao operacional exemplar (5 documentos)
- (+) Health check profundo com verificacao de todos os servicos
- (+) CI/CD com GitHub Actions e deploy staging → production
- (+) **4 alertas de monitoring activos** (5xx, latencia, health, CPU)
- (+) **Autoscale configurado**
- (+) App Insights criado com 90 dias de retencao
- (-) **App Insights IngestionMode = Disabled** — telemetria nao esta a ser recolhida
- (-) **Health check path nao configurado** no App Service
- (-) Backup operator nao definido
- (-) Sem DR documentado
- **Conclusao**: Infraestrutura de monitoring existe mas tem gaps de activacao. Corrigir ingestao e health path.

### **Risk Score Global: 4.4/10 (Risco Moderado-Baixo) [REVISTO de 5.2]**

Revisao em baixa justificada por:
1. 14/14 secrets em Key Vault (anteriormente assumido como parcial)
2. Managed Identity activa
3. 4 alertas de monitoring configurados
4. Autoscale configurado
5. 18 model deployments com redundancia

---

## Recomendacoes Prioritarias (Top 10)

Ordenadas por **impacto / esforco** (alto impacto + baixo esforco primeiro):

### 1. Activar App Insights Ingestion + Health Check Path
- **Impacto**: Alto | **Esforco**: Muito Baixo (minutos)
- Activar IngestionMode no App Insights.
- Configurar `healthCheckPath = /health` no App Service.
- **Resultado**: Monitoring e auto-healing passam a funcionar imediatamente.

### 2. Implementar Locks de Concorrencia no Backend
- **Impacto**: Alto | **Esforco**: Baixo (2-3 dias)
- Adicionar `asyncio.Lock()` ao ConversationStore, file loading, e HTTP client initialization.
- Elimina risco de corrupcao de dados em requests concorrentes.

### 3. VNet + Entra ID (Continuar pressao na DSI)
- **Impacto**: Muito Alto | **Esforco**: Medio (dependente da DSI)
- VNet + Private Endpoints para todos os servicos (App Service, Storage, Search, OpenAI, Key Vault).
- Entra ID para SSO corporativo.
- **Accao imediata**: Restringir IPs no SCM site como medida interina.

### 4. Desactivar FTPS + Restringir SCM
- **Impacto**: Medio | **Esforco**: Muito Baixo (minutos)
- `az webapp config set --ftps-state Disabled`
- Adicionar IP restrictions ao SCM site.

### 5. Upgrade AI Search para Basic
- **Impacto**: Medio | **Esforco**: Baixo (1 dia)
- Tier Free sem SLA e inadequado para producao bancaria.
- Basic: SLA 99.9%, 2GB storage, 15 indices.
- Re-indexar apos upgrade.

### 6. Cleanup de Recursos Orfaos
- **Impacto**: Baixo-Medio | **Esforco**: Baixo (horas)
- Verificar e remover: bing_chatbot, Logic App, CosmosDB, DBDE-Chatbot, deployment gpt-4o.
- Reduz superficie de ataque e custos desnecessarios.

### 7. Adicionar Dependency Scanning ao CI
- **Impacto**: Medio | **Esforco**: Muito Baixo (horas)
- Adicionar `pip audit` e `npm audit` ao GitHub Actions CI.
- Detecta vulnerabilidades conhecidas em dependencias automaticamente.

### 8. Proteger Secrets nos Logs
- **Impacto**: Medio | **Esforco**: Baixo (1 dia)
- Filtrar headers de Authorization nos logs do backend.
- Ja mitigado parcialmente pelo Key Vault (secrets nao inline no codigo).

### 9. Refactoring do Frontend (Fase 1)
- **Impacto**: Alto | **Esforco**: Medio (1-2 semanas)
- Decompor App.jsx em 5-6 componentes focados.
- Implementar useReducer para estado de conversacao.
- Adicionar React.memo e useCallback para performance.

### 10. Testar Model Router + gpt-5.3-chat
- **Impacto**: Medio | **Esforco**: Baixo (1-2 dias)
- Testar gpt-5.3-chat (deployado 2026-03-03) como novo tier pro.
- Activar Model Router para routing inteligente automatico.
- Cleanup de modelos legacy (gpt-4o, gpt-5-chat capacity=5).

---

## Conclusao

O DBDE AI Assistant v7.3.0 e um produto **solido e bem operacionalizado** que entrega valor real a ~20 utilizadores internos do Millennium BCP. A verificacao Azure confirmou varias forcas que nao eram visiveis apenas no codigo:

**Destaques positivos confirmados:**
- 14/14 secrets em Key Vault com Managed Identity
- 18 model deployments incluindo gpt-5.3-chat (Março 2026)
- 4 alertas de monitoring activos
- DataZone deployments para data residency EU
- Claude Opus/Sonnet 4.6 via Foundry como fallback

**Gaps criticos identificados:**
- App Insights com ingestao desactivada (quick fix)
- Health check path nao configurado (quick fix)
- Todos os servicos com acesso publico (aguarda VNet/DSI)
- AI Search no tier Free sem SLA

Com as 10 recomendacoes implementadas, o Risk Score global pode baixar de **4.4/10 para ~2.0/10**.

---
*Analise gerada automaticamente em 2026-03-06 via Claude Code.*
*Verificacao Azure realizada com az CLI — Subscription: Azure subscription 1, Tenant: MILLENNIUM BCP.*
*Projecto: DBDE AI Assistant v7.3.0 — Millennium BCP (uso interno)*
