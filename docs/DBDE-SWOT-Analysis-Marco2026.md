# DBDE AI Assistant v7.3.0 — Analise SWOT Completa
## Data: 6 de Marco de 2026
## Autor: Analise automatizada via Claude Code
## Owner: Pedro Mousinho, Product Owner — Millennium BCP

---

## Resumo Executivo

O DBDE AI Assistant v7.3.0 e um produto interno maduro e bem arquitectado, com 5 features de seguranca/produtividade implementadas (PII Shield, Code Interpreter, Structured Outputs, Prompt Shield, Document Intelligence), 150 testes a passar, e documentacao operacional solida. A stack (FastAPI + React + Azure) e adequada para o caso de uso (~20 utilizadores internos). O risco global e **moderado (5.2/10)** — a aplicacao esta funcional e segura para o contexto actual, mas existem fragilidades de concorrencia, gaps de hardening e oportunidades de optimizacao significativas. As prioridades imediatas sao: hardening de concorrencia no backend, refactoring do frontend monolitico, e implementacao de VNet + Entra ID (dependente da DSI).

---

## STRENGTHS (Forcas)

### S1. Arquitectura de Seguranca em Camadas
- **5 shields activos**: PII masking (Azure AI Language), Prompt Shield (Content Safety), Code Interpreter sandboxed, Structured Outputs (JSON schemas validados), Document Intelligence (OCR seguro).
- **Abuse Monitoring Opt-Out aprovado pela Microsoft** — zero data retention nos modelos Azure OpenAI, eliminando risco de dados bancarios serem usados para treino.
- **Fail-open design** nos shields — se um servico de seguranca falha, a app continua a funcionar (degradacao graceful em vez de hard failure).

### S2. Pipeline LLM Robusto
- **Multi-provider com fallback**: Azure OpenAI (primario) + Anthropic via Foundry (fallback), com tracking explicito de qual provider serviu cada request.
- **Sistema de tiers** (fast/standard/pro/vision) permite optimizacao de custo vs qualidade por tipo de operacao.
- **Streaming suportado** para ambos os providers com chunked JSON parsing sofisticado.
- **Retry com exponential backoff** (3-30s) para erros transientes.

### S3. Code Interpreter com Sandbox Seguro
- **AST-based static analysis** antes de execucao — bloqueia imports perigosos (subprocess, socket, ctypes, pickle).
- **Allowlist de imports seguros** (pandas, numpy, matplotlib, seaborn, plotly).
- **Isolamento por subprocess** com environment hardening (HOME/TMPDIR custom).
- **Limites de ficheiros**: upload 50MB, output 10MB, timeout 240s.
- **Safe patching** de `open()` e `plt.show()` para comportamento transparente na sandbox.

### S4. Documentacao Operacional Exemplar
- **5 documentos de operacao**: CONTINUITY.md, RUNBOOK.md, DEPLOY_CHECKLIST.md, THIRD_PARTY_INVENTORY.md, DATA_POLICY.md.
- **Health check profundo** (`/health?deep=true`) com verificacao de todos os servicos: storage, LLM, search, workers.
- **Deploy com staging slot** e swap controlado, com smoke test e rollback documentado.
- **Matriz de criticidade** por servico externo documentada.

### S5. CI/CD e Qualidade
- **150 testes a passar** (1 skipped) com cobertura de 4 camadas (RAG, Tools, Arena, User Story).
- **GitHub Actions CI** com matrix Python 3.11/3.12 + frontend build verification.
- **Deploy checklist** formal com pre-deploy, staging, swap, pos-deploy e rollback.

### S6. Autenticacao Solida (para o contexto)
- **JWT custom sem dependencias externas** — usa stdlib hmac/hashlib.
- **PBKDF2 com 100k iteracoes** para password hashing.
- **Token rotation** suportada (current + previous secret).
- **Context variable** para isolamento multi-request.

### S7. Gestao de Custos
- **Custo actual ~30-55 EUR/mes** — extremamente eficiente para a funcionalidade entregue.
- **App Service B1** adequado para ~20 utilizadores.
- **Sistema de quotas** por tier LLM com tracking por utilizador.

### S8. Integracao Rica de Ferramentas
- **DevOps integration** nativa (work items, queries WIQL).
- **Figma + Miro APIs** para contexto de design/produto.
- **Brave Search** para pesquisa web actual.
- **AI Search** para RAG com reranking.
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

### W3. JWT Secret em Producao
- Se `JWT_SECRET` nao e definido explicitamente, o sistema gera um **secret efemero** — cada restart invalida todos os tokens.
- Num cenario multi-instancia (scale-out), cada replica teria secrets diferentes → tokens incompativeis entre replicas.
- **Deveria hard-fail** em producao em vez de gerar secrets fracos.

### W4. Sem Token Blacklist / Refresh
- **Logout apenas apaga cookie** — token continua valido ate expirar (10h default).
- **Sem refresh tokens** — sessoes longas forcam re-autenticacao.
- **Sem rate limiting** em tentativas de autenticacao falhadas.

### W5. Logging Pode Expor Secrets
- **DevOps PAT** incluido em headers de Authorization que podem ser logged via `logging.info()`.
- **Erros de API** truncados a 300 chars — pode esconder informacao critica em debug, mas tambem pode expor dados sensiveis.
- **Sem filtragem de headers** nos logs.

### W6. PII Shield — Bug de Overlapping Entities
- Se duas entidades PII se sobrepoem (ex: "Joao Silva" detectado como pessoa + "Joao" detectado individualmente), a segunda substituicao pode corromper o texto masked.
- **HTTP client criado por request** — ineficiente, deveria ser reutilizado.

### W7. Parsing Numerico Fragil
- Logica de conversao de separadores PT (virgula decimal, ponto milhares) e **ambigua** — "1,000" pode ser interpretado como 1.0 em vez de 1000.
- Afecta tools de analise de dados e code interpreter.

### W8. Ausencia de Middleware de Validacao
- Nem todos os endpoints validam Content-Type ou tamanho de request.
- **CORS hardcoded** para apenas 2 origens — fragil para adicionar novos dominios.

### W9. Document Intelligence com Polling Fixo
- Polling cada 2 segundos durante 60 segundos maximo — desperdiça tempo em documentos rapidos que completam em <2s.
- Sem telemetria de performance (tempo de analise, paginas/segundo).

### W10. Code Interpreter — Gaps de Hardening
- **PATH copiado do parent** — utilizador pode potencialmente chamar binarios do sistema.
- **Sem limites de CPU/memoria** no subprocess (apenas timeout).
- **Symlink attack possivel** — `_safe_path()` nao valida symlinks.
- **Per-request mount limit** de 100MB pode causar memory exhaustion.

---

## OPPORTUNITIES (Oportunidades)

### O1. VNet + Entra ID (Dependente da DSI)
- **VNet integration** eliminaria exposicao publica do App Service — comunicacao interna apenas.
- **Entra ID (Azure AD)** substituiria autenticacao JWT custom por SSO corporativo — eliminando gestao de passwords.
- **Impacto**: Reduziria drasticamente a superficie de ataque e simplificaria onboarding de utilizadores.

### O2. Refactoring Frontend
- Decomposicao em componentes focados (ChatContainer, MessageList, InputForm, FileUpload, ConversationList).
- **useReducer** para estado de conversacao + **custom hooks** (useConversations, useUpload, useChat).
- **React.memo + useCallback** para eliminar rerenders.
- **Virtualizacao** de mensagens com react-window para conversas longas.
- **TypeScript** para type safety.

### O3. AI Search Re-Index
- Re-indexacao dos indices (DevOps, Omni, Examples) pode melhorar significativamente a qualidade do RAG.
- **Oportunidade**: Adicionar indices tematicos (documentacao tecnica, guidelines do banco, melhores praticas).

### O4. Optimizacao de Modelos
- **gpt-4.1-mini** para operacoes de routing/classificacao (mais barato, mais rapido).
- **gpt-5.1** como tier pro para tarefas complexas.
- **Structured Outputs** podem ser expandidos para mais tipos de resposta (analise de risco, estimativas, comparacoes).

### O5. Melhorias no Code Interpreter
- Adicionar suporte para mais bibliotecas (scikit-learn, statsmodels para analise estatistica).
- **Resource limits** (CPU/memoria) via `resource.setrlimit()`.
- **Symlink validation** e PATH hardening.
- Adicionar preview de outputs graficos inline.

### O6. Monitoring e Observabilidade
- **App Insights integration** com custom metrics: latencia por tool, taxa de erros por provider, uso de tokens.
- **Alertas proactivos**: quota LLM a 80%, PAT DevOps proximo de expirar, storage a atingir limites.
- **Dashboard operacional** com metricas em tempo real.

### O7. Export e Reporting Avancado
- **Gerar relatorios automaticos** de user stories com formato padrao do banco.
- **Export para Confluence/SharePoint** alem de DOCX/PDF.
- **Templates** personalizaveis por equipa.

### O8. Multi-Idioma e Acessibilidade
- Suportar ingles alem de portugues para equipas internacionais.
- **WCAG compliance** no frontend para acessibilidade.

### O9. Caching e Performance
- **Cache de respostas LLM** para queries repetidas (ex: definicoes, processos standard).
- **CDN para assets estaticos** com fallback local (ja parcialmente implementado).
- **Connection pooling** para HTTP clients.

### O10. Integracao com Mais Ferramentas
- **Jira** para equipas que nao usam Azure DevOps.
- **Confluence** para knowledge base.
- **Teams** para notificacoes e interacao directa.
- **GitLab/GitHub** para code review assistido.

---

## THREATS (Ameacas)

### T1. Risco de Dados Bancarios Confidenciais
- Mesmo com PII Shield activo, utilizadores podem inadvertidamente colar dados sensíveis de clientes (NIFs, numeros de conta, moradas).
- **PII Shield tem threshold de 0.7** — entidades com confidence <0.7 passam sem mascaramento.
- **Dados em transito** para Azure OpenAI, Anthropic, Brave Search — multiplos pontos de exposicao.
- **Mitigacao actual**: Data Policy documentada, Abuse Monitoring Opt-Out activo (zero retention).
- **Risco residual**: Depende do comportamento dos utilizadores e da eficacia do PII Shield.

### T2. Dependencia de Servicos Azure
- **Azure OpenAI**: Se rate-limited (429) ou indisponivel, fallback para Anthropic pode nao ter feature parity completa.
- **Azure AI Search**: Se indisponivel, RAG nao funciona — sem fallback.
- **Azure Storage**: Single point of failure para estado da aplicacao e ficheiros.
- **Nenhuma estrategia de DR** (disaster recovery) documentada alem de rollback de deploy.

### T3. Supply Chain e Dependencias
- **19 dependencias Python** + **dependencias npm** — cada uma e um vector de ataque potencial.
- **CDNs externos** (cdnjs, plot.ly, Google Fonts) — risco de comprometimento de supply chain.
- **Mitigacao parcial**: Fallback local em `static/vendor/` para CDNs.
- **Sem `pip audit` ou `npm audit`** no CI pipeline.

### T4. Escalabilidade Limitada
- **App Service B1** com **1 worker Uvicorn** — nao escala horizontalmente.
- **ConversationStore in-memory** com LRU eviction — nao partilhado entre instancias.
- Se o numero de utilizadores crescer significativamente (>50), a arquitectura actual nao suporta.
- **Scale-out** exigiria: externalizacao de estado (Redis/Table Storage), JWT secret partilhado, session affinity.

### T5. Regulacao Bancaria
- **EBA/BCE guidelines** sobre uso de AI em instituicoes financeiras podem exigir auditorias adicionais.
- **RGPD/GDPR**: Processamento de dados pessoais (mesmo internos) requer base legal e DPO notification.
- **DSI do banco** pode impor restricoes adicionais apos auditoria.
- **Classificacao de dados**: Nao existe validacao automatica do nivel de classificacao dos documentos uploaded.

### T6. Expiracao de Credenciais
- **DevOps PAT** expira periodicamente — se nao renovado, tools de DevOps param.
- **API keys** (OpenAI, Search, Brave, Figma, Miro) — nao ha mecanismo automatico de rotacao.
- **Sem alertas proactivos** de expiracao proxima.

### T7. Prompt Injection Avancado
- **Prompt Shield** detecta ataques conhecidos, mas ataques sofisticados (jailbreaks multi-turn, injection via documentos) podem bypass.
- **Code Interpreter** — apesar do sandboxing, edge cases no AST checker (imports relativos, __import__) podem permitir bypass.
- **Document Intelligence** — documentos maliciosos podem injectar instrucoes via texto extraido.

### T8. Vendor Lock-in
- Forte dependencia de Azure (OpenAI, Storage, Search, App Service, Content Safety, Document Intelligence, AI Language).
- Migracao para outro cloud provider seria complexa e custosa.
- **Anthropic Foundry** como fallback e o unico ponto de diversificacao.

---

## Risk Score (0-10)

### Seguranca Aplicacional: 4.5/10 (Risco Moderado)
**Justificacao:**
- (+) PII Shield, Prompt Shield, Code Interpreter sandbox, DOMPurify no frontend
- (+) Abuse Monitoring Opt-Out activo (zero data retention)
- (+) JWT auth com PBKDF2 100k iterations
- (-) Concorrencia nao thread-safe pode causar corrupcao de dados
- (-) Sem token blacklist — tokens leaked ficam validos 10h
- (-) Sem rate limiting em autenticacao
- (-) DevOps PAT pode leakar em logs
- (-) Code Interpreter PATH e symlink gaps
- **Conclusao**: Seguranca adequada para ~20 utilizadores internos, mas necessita hardening antes de escalar.

### Seguranca de Dados/Rede: 5.5/10 (Risco Moderado-Alto)
**Justificacao:**
- (+) PII masking antes de envio para LLM
- (+) Data Policy documentada com dados proibidos claros
- (+) Zero data retention (Abuse Monitoring Opt-Out)
- (-) **Sem VNet** — App Service exposto publicamente (dependente da DSI)
- (-) **Sem Entra ID** — auth custom em vez de SSO corporativo
- (-) Multiplos servicos externos recebem dados potencialmente sensiveis
- (-) PII Shield com threshold 0.7 pode falhar em entidades ambiguas
- (-) Storage keys em app settings em vez de Key Vault (parcialmente)
- **Conclusao**: O risco principal e a exposicao publica sem VNet e a dependencia do comportamento dos utilizadores para nao colar dados sensíveis.

### Custo/Sustentabilidade: 2.0/10 (Risco Baixo)
**Justificacao:**
- (+) Custo actual 30-55 EUR/mes e muito eficiente
- (+) Sistema de tiers LLM optimiza custo vs qualidade
- (+) App Service B1 adequado para carga actual
- (+) Orcamento flexivel (custo nao e factor limitante)
- (-) Sem alertas de custo configurados
- (-) Sem tracking granular de custo por feature/utilizador
- **Conclusao**: Custo excelente. Risco minimo nesta area.

### Qualidade de Codigo: 5.0/10 (Risco Moderado)
**Justificacao:**
- (+) Backend bem estruturado com separacao de concerns
- (+) 150 testes a passar com boa cobertura
- (+) Async/await patterns consistentes
- (+) Error handling razoavel com fail-open design
- (-) Frontend monolitico (1,872 linhas em App.jsx, 50+ estados)
- (-) Sem TypeScript no frontend
- (-) Race conditions em multiplos componentes backend
- (-) Numeric parsing fragil
- (-) Magic numbers e strings scattered
- (-) Sem useCallback/useMemo/React.memo no frontend
- **Conclusao**: Backend e solidamente acima da media; frontend precisa de refactoring significativo.

### Operacoes/Monitoring: 4.0/10 (Risco Moderado)
**Justificacao:**
- (+) Documentacao operacional exemplar (Runbook, Continuity, Deploy Checklist)
- (+) Health check profundo com verificacao de todos os servicos
- (+) CI/CD com GitHub Actions e deploy staging → production
- (+) Smoke tests e rollback documentados
- (-) **Sem alertas proactivos** de expiracao de credentials
- (-) **App Insights** possivelmente nao configurado / ingestao nao confirmada
- (-) Sem dashboard operacional com metricas em tempo real
- (-) Sem audit trail de accoes de administracao
- (-) Backup operator nao definido ("Backup operacional: definir")
- (-) Sem DR (disaster recovery) documentado
- **Conclusao**: Operacoes bem documentadas mas reactivas. Falta proactividade em monitoring e alertas.

### **Risk Score Global: 5.2/10 (Risco Moderado)**

---

## Recomendacoes Prioritarias (Top 10)

Ordenadas por **impacto / esforco** (alto impacto + baixo esforco primeiro):

### 1. Implementar Locks de Concorrencia no Backend
- **Impacto**: Alto | **Esforco**: Baixo (2-3 dias)
- Adicionar `asyncio.Lock()` ao ConversationStore, file loading, e HTTP client initialization.
- Elimina risco de corrupcao de dados em requests concorrentes.

### 2. VNet + Entra ID (Continuar pressao na DSI)
- **Impacto**: Muito Alto | **Esforco**: Medio (dependente da DSI)
- VNet elimina exposicao publica. Entra ID elimina JWT custom.
- **Accao imediata**: Preparar documentacao tecnica para facilitar o trabalho da DSI.

### 3. Proteger Secrets nos Logs
- **Impacto**: Alto | **Esforco**: Baixo (1 dia)
- Filtrar headers de Authorization nos logs.
- Migrar DevOps PAT e outras keys sensiveis para Key Vault.

### 4. JWT Secret Hard-Fail em Producao
- **Impacto**: Alto | **Esforco**: Muito Baixo (horas)
- Se `JWT_SECRET` nao e definido e estamos em producao (`WEBSITE_SITE_NAME` existe), hard-fail no startup.
- Adicionar token blacklist in-memory com TTL.

### 5. Adicionar Dependency Scanning ao CI
- **Impacto**: Medio | **Esforco**: Muito Baixo (horas)
- Adicionar `pip audit` e `npm audit` ao GitHub Actions CI.
- Detecta vulnerabilidades conhecidas em dependencias automaticamente.

### 6. Refactoring do Frontend (Fase 1)
- **Impacto**: Alto | **Esforco**: Medio (1-2 semanas)
- Decompor App.jsx em 5-6 componentes focados.
- Implementar useReducer para estado de conversacao.
- Adicionar React.memo e useCallback para performance.

### 7. Configurar App Insights com Custom Metrics
- **Impacto**: Medio | **Esforco**: Baixo (2-3 dias)
- Integrar OpenTelemetry ou Azure Monitor SDK.
- Metricas: latencia por tool, erros por provider, tokens consumidos.
- Alertas: quota LLM >80%, erros 5xx, PAT proximo de expirar.

### 8. AI Search Re-Index com Indices Tematicos
- **Impacto**: Medio | **Esforco**: Medio (3-5 dias)
- Re-indexar indices existentes com embeddings actualizados.
- Considerar indices adicionais (documentacao tecnica, guidelines).

### 9. Code Interpreter Hardening
- **Impacto**: Medio | **Esforco**: Baixo (1-2 dias)
- Hardcode PATH minimo (`/usr/local/bin:/usr/bin`).
- Adicionar `resource.setrlimit()` para CPU/memoria.
- Validar symlinks em `_safe_path()`.

### 10. Definir Backup Operator e Plano de DR
- **Impacto**: Medio | **Esforco**: Baixo (1-2 dias)
- Designar backup operator com acessos documentados.
- Documentar plano de DR basico (RTO/RPO para cada servico).
- Configurar alertas de expiracao de credentials.

---

## Nota sobre Verificacao Azure

A verificacao da infraestrutura Azure (App Service config, model deployments, Cognitive Services, AI Search indices, Key Vault, App Insights) **nao pode ser realizada neste ambiente** porque o Azure CLI nao esta instalado. Recomenda-se executar os seguintes comandos manualmente:

```bash
# Login
az login --use-device-code

# App Service
az webapp show -n <app-name> -g <resource-group> --query "{state:state, kind:kind, httpsOnly:httpsOnly}"
az webapp config show -n <app-name> -g <resource-group> --query "{minTlsVersion:minTlsVersion, ftpsState:ftpsState, http20Enabled:http20Enabled}"
az webapp config appsettings list -n <app-name> -g <resource-group> --query "[].name" -o tsv

# Azure OpenAI Deployments
az cognitiveservices account deployment list -n <openai-resource> -g <resource-group> -o table

# AI Search Indices
az search index list --service-name <search-service> -g <resource-group> -o table

# Key Vault
az keyvault secret list --vault-name <vault-name> -o table

# App Insights
az monitor app-insights component show -a <appinsights-name> -g <resource-group> --query "{ingestionEndpoint:ingestionEndpoint, connectionString:connectionString}"

# Recursos orfaos
az resource list -g <resource-group> -o table
```

---

## Conclusao

O DBDE AI Assistant v7.3.0 e um produto **solido e funcional** que entrega valor real a ~20 utilizadores internos do Millennium BCP. A arquitectura de seguranca em camadas (5 shields), o pipeline LLM multi-provider, e a documentacao operacional sao pontos fortes significativos. O Abuse Monitoring Opt-Out aprovado pela Microsoft e uma vantagem competitiva importante para o contexto bancario.

As areas que requerem atencao imediata sao: **concorrencia no backend** (locks), **proteccao de secrets nos logs**, e **continuacao da pressao para VNet + Entra ID**. O frontend precisa de refactoring mas nao e bloqueante para operacao.

Com as 10 recomendacoes implementadas, o Risk Score global pode baixar de **5.2/10 para ~2.5/10**.

---
*Analise gerada automaticamente em 2026-03-06 via Claude Code.*
*Projecto: DBDE AI Assistant v7.3.0 — Millennium BCP (uso interno)*
