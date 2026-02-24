# Auditoria Final Pós-Fase 7 (v7.2.1)

Data: 2026-02-24  
Escopo auditado: `app.py`, `tools.py`, `agent.py`, `llm_provider.py`, `export_engine.py`, `storage.py`, `tools_figma.py`, `tools_miro.py`, `config.py`, `models.py`, `learning.py`, `auth.py`, `tool_registry.py`, `start_server.py`, `startup.sh`, `requirements.txt`.

## Findings (ordenados por severidade)

### CRÍTICO

1. **Leak cross-user em pesquisa de anexos (corrigido)**  
   - Ficheiro/linhas: `tools.py:501-517`, `tools.py:568-585`, `agent.py:741-767`, `agent.py:862-866`, `agent.py:975-979`, `app.py:1083-1087`  
   - Categoria: **2. Segurança**  
   - Severidade: **CRÍTICO**  
   - Descrição: `search_uploaded_document` carregava chunks por `conv_id` sem filtrar `UserSub`, permitindo leitura potencial de anexos de outra conversa/utilizador se o `conv_id` fosse conhecido.  
   - Recomendação: manter filtro obrigatório por `UserSub` em index + fallback de memória com o mesmo filtro, e injetar `user_sub` no dispatcher de tools.
   - Estado: **Corrigido nesta auditoria**.

2. **`/health` público com operações caras (corrigido)**  
   - Ficheiro/linhas: `app.py:2217-2248`  
   - Categoria: **2. Segurança** / **3. Liabilities**  
   - Severidade: **CRÍTICO**  
   - Descrição: endpoint público fazia embeddings + chamadas Search em cada request, criando vetor de custo/DoS e ruído operacional.  
   - Recomendação: health básico público (barato), health profundo apenas autenticado (admin), com rate limit.
   - Estado: **Corrigido nesta auditoria** (modo básico + `deep=true` admin + limiter).

### ALTO

3. **Ausência de rate limit em endpoints sensíveis autenticados**  
   - Ficheiro/linhas: `app.py:1925`, `app.py:1936`, `app.py:1951`, `app.py:1962`, `app.py:1978`, `app.py:2019`, `app.py:2030`  
   - Categoria: **2. Segurança**  
   - Severidade: **ALTO**  
   - Descrição: vários endpoints com escrita/consulta intensiva não têm `@limiter`, aumentando superfície para brute force lógico e abuso de recursos com sessão válida.  
   - Recomendação: aplicar limites por utilizador/IP por família de endpoint (auth admin, feedback, chats).

4. **Fallback de `JWT_SECRET` não bloqueia arranque sem segredo explícito**  
   - Ficheiro/linhas: `config.py:112-132`  
   - Categoria: **2. Segurança**  
   - Severidade: **ALTO**  
   - Descrição: em ausência de `JWT_SECRET`, a app deriva segredo de outros segredos ou gera efémero. É melhor que hardcoded, mas mantém risco operacional e rotação não controlada.  
   - Recomendação: falhar startup em produção sem `JWT_SECRET` explícito (feature flag por ambiente).

5. **Construção de URL Table Storage com chaves não escapadas na operação MERGE/DELETE**  
   - Ficheiro/linhas: `storage.py:349-375`  
   - Categoria: **1. Bugs** / **2. Segurança**  
   - Severidade: **ALTO**  
   - Descrição: `PartitionKey`/`RowKey` entram na URL sem escape defensivo consistente; pode causar falhas em keys especiais e edge cases difíceis de depurar.  
   - Recomendação: normalizar e escapar chaves em literal OData para URL/resource string.

### MÉDIO

6. **Digest executa secções em série**  
   - Ficheiro/linhas: `app.py:1792-1793`  
   - Categoria: **4. Melhorias** / **6. Inconsistências**  
   - Severidade: **MÉDIO**  
   - Descrição: 4 secções de digest são executadas sequencialmente, aumentando latência.  
   - Recomendação: executar com `asyncio.gather` e timeout por secção.

7. **Duplicação de `feedback_memory` em módulos diferentes**  
   - Ficheiro/linhas: `app.py:268`, `storage.py:34`  
   - Categoria: **6. Inconsistências**  
   - Severidade: **MÉDIO**  
   - Descrição: existem dois stores de fallback (`app.feedback_memory` e `storage.feedback_memory`) com semânticas diferentes.  
   - Recomendação: consolidar fallback num único módulo/contrato.

8. **Stores in-memory ainda críticos para escala**  
   - Ficheiro/linhas: `agent.py:46-166`, `agent.py:152`, `tools.py:35-39`  
   - Categoria: **3. Liabilities**  
   - Severidade: **MÉDIO**  
   - Descrição: `ConversationStore`, `uploaded_files_store` e `_generated_files_store` continuam a depender de memória local (apesar dos caps).  
   - Recomendação: mover estado transitório chave para storage distribuído/cache partilhada quando fechar hardening de escala.

9. **`llm_provider` usa `print` em vez de logger estruturado**  
   - Ficheiro/linhas: `llm_provider.py:38`  
   - Categoria: **4. Melhorias**  
   - Severidade: **MÉDIO**  
   - Descrição: logging inconsistente dificulta observabilidade e correlação no App Insights.  
   - Recomendação: substituir por logger com níveis e contexto (provider/tier/request id).

10. **Defaults mutáveis em modelos Pydantic**  
   - Ficheiro/linhas: `models.py:36-38`, `models.py:103`, `models.py:143-144`, `models.py:166`  
   - Categoria: **1. Bugs** / **4. Melhorias**  
   - Severidade: **MÉDIO**  
   - Descrição: uso de `[]`/`{}` em defaults é propenso a erro e ambiguidade de manutenção.  
   - Recomendação: migrar para `Field(default_factory=list/dict)`.

### BAIXO

11. **Headers/version strings desatualizados em vários ficheiros**  
   - Ficheiro/linhas: `agent.py:2`, `llm_provider.py:2`, `export_engine.py:2`, `storage.py:2`, `auth.py:2`, `models.py:2`  
   - Categoria: **6. Inconsistências**  
   - Severidade: **BAIXO**  
   - Descrição: comentários de versão (`v7.0`) não refletem estado atual.  
   - Recomendação: uniformizar headers para `v7.2.1` (ou remover versão de comentário e manter apenas `APP_VERSION`).

12. **`start_server.py` aparenta obsoleto no runtime atual**  
   - Ficheiro/linhas: `start_server.py:1-9`, `startup.sh:20`  
   - Categoria: **5. Código desnecessário**  
   - Severidade: **BAIXO**  
   - Descrição: startup produtivo usa `uvicorn app:app`; `start_server.py` não é utilizado no fluxo atual.  
   - Recomendação: confirmar usos externos; se não houver, remover ou documentar uso legacy.

13. **Compat layer `TOOLS = get_all_tool_definitions()` potencialmente legado**  
   - Ficheiro/linhas: `tools.py:1460-1461`  
   - Categoria: **5. Código desnecessário**  
   - Severidade: **BAIXO**  
   - Descrição: compatibilidade antiga pode não ser necessária após migração para `tool_registry`.  
   - Recomendação: confirmar imports reais e eliminar compat se não houver consumidores.

## Alterações aplicadas nesta auditoria (apenas CRÍTICOS)

- `tools.py`: filtro obrigatório por `user_sub` na leitura de chunks persistidos e fallback em memória.  
- `agent.py`: injecção interna de `user_sub` na tool `search_uploaded_document`.  
- `app.py`: grava `user_sub` no `store_entry` de upload e `health` básico público + `health?deep=true` admin + rate limit.

## Validação executada

- Runtime hash verification (`/api/runtime/check` + manifest): **OK** para `app.py`, `agent.py`, `tools.py`, `config.py`, `static/index.html`.  
- `GET /health`: **200**, modo básico barato.  
- `GET /api/info`: **200**, `version="7.2.1"`, `active_tools=15` (não 14; o valor esperado antigo está desatualizado).  
- `GET /api/digest` com auth: **200**.

## Nota de versão

- `APP_VERSION` já estava em `7.2.1`; não foi necessário novo bump nesta auditoria.
