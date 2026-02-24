# Fix Figma/Miro Tool Routing — Instruções para o Codex
## Emitido por: Claude (Arquiteto) | Data: 2026-02-23
## Prioridade: P0 — Bloqueia fecho da Fase 5

---

## DIAGNÓSTICO

### Problema 1: Prompt gate exclui Figma/Miro

O system prompt em `get_agent_system_prompt()` (tools.py, linhas 1132-1145) tem uma regra prioritária que funciona como gate:

```
REGRA PRIORITÁRIA — RESPOSTA DIRECTA SEM FERRAMENTAS:
Antes de decidir qual ferramenta usar, avalia se a pergunta PRECISA de dados do DevOps, AI Search ou site MSE.
Se NÃO precisa, responde DIRETAMENTE sem chamar nenhuma ferramenta.
```

**Figma e Miro não são mencionados neste gate.** O LLM avalia a pergunta contra "DevOps, AI Search ou site MSE", conclui que não precisa, e responde directamente — nunca chega às routing rules 11/12 mais abaixo.

A linha 1144 agrava: "Na dúvida entre responder directamente ou usar ferramenta, prefere responder directamente."

### Problema 2: Ausência de observabilidade do registry

O `/api/info` não reporta quais tools estão activas. Não temos como confirmar remotamente se `search_figma` e `search_miro` se registaram em produção.

---

## CORREÇÕES

### Fix 1: Actualizar o gate do system prompt (tools.py)

**Ficheiro:** `tools.py`, função `get_agent_system_prompt()`

**Objectivo:** O gate deve mencionar Figma e Miro como fontes de dados válidas quando as tools estiverem activas.

**O que fazer:**

A linha 1133 actualmente diz:
```
Antes de decidir qual ferramenta usar, avalia se a pergunta PRECISA de dados do DevOps, AI Search ou site MSE.
```

Deve ser dinâmica, incluindo Figma/Miro quando activos. Exemplo:
```
Antes de decidir qual ferramenta usar, avalia se a pergunta PRECISA de dados do DevOps, AI Search, site MSE{, Figma}{, Miro}.
```

Onde `{, Figma}` e `{, Miro}` só são injectados se `has_tool("search_figma")` / `has_tool("search_miro")` forem true.

Também na secção de "Categorias que NÃO precisam de ferramentas" (linhas 1136-1142), **NÃO adicionar Figma/Miro** — estas categorias são correctas como estão. A mudança é apenas no gate de avaliação inicial.

### Fix 2: Reforçar as routing rules Figma/Miro

**Ficheiro:** `tools.py`, mesma função

**Objectivo:** As rules 11 e 12 (Figma/Miro) devem ter peso equivalente às outras regras, não serem um afterthought.

**O que fazer:**

No bloco de `integration_routing` (linhas 1091-1111), reforçar a formulação. As regras actuais dizem "(OBRIGATORIO)" mas estão depois do gate que já cortou a decisão.

Sugestão: mover as regras Figma/Miro para DENTRO do bloco de routing principal (linhas 1151-1176), não como apêndice via `{integration_routing}` no final. Integrar naturalmente:
- Regra 11 (Figma) ao lado das regras 9/10
- Regra 12 (Miro) logo a seguir

Isto garante que o LLM as vê no mesmo contexto visual que as outras routing rules, não como addendum.

### Fix 3: Adicionar tools activas ao /api/info

**Ficheiro:** `app.py`, endpoint `/api/info`

**Objectivo:** Observabilidade. Saber remotamente se as tools se registaram.

**O que fazer:**

Adicionar ao response do `/api/info`:
```python
"active_tools": get_registered_tool_names(),
```

Isto importa `get_registered_tool_names` de `tool_registry` e retorna a lista de nomes registados. Sem dados sensíveis — apenas nomes.

**Critério de sucesso:** `GET /api/info` retorna `"active_tools": ["query_workitems", "search_workitems", ..., "search_figma", "search_miro"]`

---

## VALIDAÇÃO

Após as 3 correções:

1. **Deploy** (VFS PUT de tools.py + app.py + restart, OU ZIP deploy)
2. **Verificar /api/info** → `active_tools` deve conter `search_figma` e `search_miro`
3. **Testar em chat:** "Lista os ficheiros recentes do Figma" → LLM deve chamar `search_figma`
4. **Testar em chat:** "Mostra os boards do Miro" → LLM deve chamar `search_miro`
5. **Teste de não-regressão:** "Quantas USs existem?" → deve continuar a chamar `query_workitems`
6. **Teste conceptual:** "O que é uma user story?" → deve responder directamente sem tools

---

## CRITÉRIO DE SUCESSO PARA FECHAR FASE 5

Todos os 4 testes (pontos 3-6 acima) a passar = Fase 5 oficialmente fechada.

---

— Claude (Arquiteto), 2026-02-23
