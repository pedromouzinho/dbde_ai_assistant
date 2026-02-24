# DBDE AI Assistant — Avaliação Estratégica v7.2.1
## Arquiteto: Claude Opus 4.6 | Data: 2026-02-24
## Para: Pedro Mousinho (Product Owner) e Codex (Lead Developer)

---

## PARTE 1 — VEREDICTO DA AUDITORIA

### Fixes Críticos: APROVADOS ✅

Os 6 fixes críticos do Codex estão correctos e bem implementados:

| # | Fix | Validação |
|---|-----|-----------|
| 1 | JWT_SECRET fallback seguro (derivado de runtime seed + logging CRITICAL) | ✅ Nunca mais usa string previsível. Fallback derivado de sha256 de outro segredo. |
| 2 | JWT exp obrigatório (encode injeta, decode rejeita sem exp) | ✅ Tokens sem expiração rejeitados. Auto-injecção de exp no encode. |
| 3 | /api/info sem traceback (pptx_status mostra "ok"/"unavailable") | ✅ Stack trace eliminado de endpoint público. |
| 4 | /health logs só em DEBUG_MODE | ✅ Logs operacionais protegidos por flag. |
| 5 | search_uploaded_document sem inferência cross-conversation | ✅ conv_id obrigatório, injectado pelo agent (linha 583-584 de agent.py). |
| 6 | Password bootstrap sem log plaintext | ✅ Aviso genérico, password nunca escrita em logs. |

### Findings Não-Críticos: Priorização Arquitectónica

| # | Finding | Severidade | Decisão |
|---|---------|-----------|---------|
| 1 | Active vs IsActive no bootstrap admin | ALTO | **Fase 7.1** — corrigir para IsActive |
| 2 | Parser CSV simplista | MÉDIO | **Fase 7.1** — usar csv module |
| 3 | uploaded_files_store sem cap de memória | ALTO | **Fase 7.1** — adicionar LRU/TTL/cap |
| 4 | Digest sequencial | MÉDIO | **Fase 7.1** — asyncio.gather |
| 5 | llm_with_fallback sem protecção final | MÉDIO | **Fase 7.1** — normalizar erro |
| 6 | /api/info expõe metadata operacional | ALTO | **Fase 7.1** — split público/admin |
| 7 | Cookie Secure depende de header | ALTO | **Fase 7.1** — env explícita |
| 8 | print em llm_provider | MÉDIO | **Fase 7.1** — substituir por logging |
| 9 | Defaults mutáveis em Pydantic | MÉDIO | **Fase 7.1** — default_factory |
| 10 | start_server.py obsoleto | BAIXO | **Fase 7.1** — remover se confirmado |
| 11 | feedback_memory morto | BAIXO | **Fase 7.1** — limpar |
| 12 | startup.sh ainda diz v7.2.0 | MÉDIO | **Fase 7.1** — alinhar com APP_VERSION |

**Nota sobre VFS PUT no deploy:** O Codex reporta que usou VFS PUT além do ZIP para "garantir runtime efectivo". Isto é uma red flag — significa que o ZIP deploy pode não estar a ser o source of truth. Na Fase 7.1, validar que o ZIP contém TODOS os ficheiros actualizados e que Oryx os extrai correctamente, sem necessidade de VFS PUT adicional.

---

## PARTE 2 — FEATURES DIFERENCIADORAS (GAME CHANGERS)

### Visão: O assistente que cria User Stories exactamente como a equipa as escreve, com o mínimo de input possível.

Hoje o DBDE AI já gera USs com base em padrões reais (analyze_patterns + generate_user_stories + WriterProfiles). Mas o fluxo ainda exige que o utilizador descreva textualmente o que quer. O próximo salto é permitir que o utilizador **mostre** em vez de descrever.

---

### FEATURE 7.A — "Screenshot to User Story" (Imagem → US completa)

**O que é:**
O utilizador faz upload de 1-2 screenshots (ex: estado actual da UI + mockup do estado desejado, ou foto de um whiteboard, ou print de um email com requisitos) e diz algo tão simples como:

- *"Quero implementar isto"*
- *"Este botão devia ficar disabled quando o campo está vazio"*
- *"Antes estava assim, agora quero assim"*

O agente analisa as imagens, identifica automaticamente os elementos UI (botões, inputs, dropdowns, labels, estados enabled/disabled, modais, toasts), compara antes vs depois se aplicável, e gera uma US completa no formato exacto do MSE — com título, descrição, ACs de Composição, Comportamento e Mockup — usando a linguagem técnico-conversacional da equipa.

**Porque é game changer:**
- O PO gasta 15-30 min a escrever uma US. Com isto, gasta 30 segundos a tirar 2 screenshots e escrever uma frase.
- As USs saem no formato exacto do board, com o vocabulário exacto ("CTA", "Enable/Disable", "Toast", "Stepper"), porque o LLM aprendeu os padrões reais.
- Reduz drasticamente a barreira para documentar melhorias pequenas que normalmente ficam por escrever.

**Como funciona tecnicamente:**
- O endpoint `/upload` já suporta imagens (caem no else genérico como texto). Precisa de ser estendido para enviar a imagem como base64 para o LLM multimodal (Claude Sonnet/Opus já suportam vision nativamente).
- O `get_userstory_system_prompt()` já tem regras de VISUAL PARSING (linhas 1482-1484 de tools.py). Mas hoje são letra morta porque o upload de imagem não passa a imagem para o LLM — só tenta extrair texto.
- A implementação real requer: guardar a imagem em base64 no `uploaded_files_store`, enviar ao LLM como mensagem multimodal `[{"type": "image_url", ...}, {"type": "text", ...}]`, e o system prompt já sabe o que fazer (identificar CTAs, inputs, labels, estados).

**Cenários concretos:**

| Input do utilizador | Output esperado |
|---------------------|-----------------|
| 2 screenshots (antes/depois) + "quero implementar esta mudança" | US com AC detalhando cada diferença visual identificada |
| Screenshot de mockup Figma + "cria US para isto" | US com AC de Composição (elementos) + Comportamento (interacções inferidas) |
| Foto de whiteboard + "isto é o que discutimos" | US extraindo texto do whiteboard e estruturando como requisitos |
| Screenshot de bug + "corrige isto" | Bug report com Steps to Reproduce extraídos do visual |
| Print de email com requisitos + "transforma em USs" | N USs decompostas do conteúdo do email |

---

### FEATURE 7.B — "Context-Aware US Refinement" (Refinamento com contexto mínimo)

**O que é:**
Quando o utilizador quer refinar ou alterar uma US existente, em vez de descrever tudo de novo, basta dar uma instrução mínima:

- *"Adiciona validação de email nesta US"*
- *"O dropdown agora tem 5 opções em vez de 3"*
- *"Muda o fluxo para ter confirmação antes de submeter"*

O agente vai ao DevOps buscar a US original (via query_workitems ou pelo ID), analisa a descrição e ACs existentes, e gera uma versão revista mantendo 100% da estrutura, linguagem e formatação HTML original — alterando apenas o necessário.

**Porque é game changer:**
- Hoje, refinar uma US significa reescrever grande parte manualmente ou copiar/colar e editar.
- Com esta feature, o PO faz micro-ajustes em 10 segundos que antes levavam 5 minutos.
- A US revista é indistinguível de uma escrita manualmente — mesmo formato HTML, mesmo nível de detalhe, mesmo vocabulário.

**Como funciona tecnicamente:**
- Combina `query_workitems` (buscar US por ID com fields Description + AcceptanceCriteria) com `generate_user_stories` (regenerar com contexto).
- O agente já sabe fazer isto em teoria (o routing permite sequenciar tools). O que falta é uma tool dedicada `refine_workitem` que recebe: work_item_id + instrução de alteração, busca o original, e gera o delta.
- O mode "userstory_writer" já tem o fluxo DRAFT → REVIEW → FINAL. A feature encaixa naturalmente como uma variante do REVIEW que parte de uma US existente em vez de zero.

---

### FEATURE 7.C — "Batch US from Figma/Miro" (Gerar USs em lote de boards visuais)

**O que é:**
O utilizador aponta para um board do Miro ou um ficheiro do Figma e diz:

- *"Gera USs para todos os frames deste Figma"*
- *"Transforma os sticky notes deste board em USs"*

O agente percorre os items do Figma/Miro, identifica componentes/fluxos distintos, e gera uma US por componente — tudo no formato do board, com referência ao source visual.

**Porque é game changer:**
- Hoje, o output de um workshop no Miro fica como sticky notes soltos. Transformar em USs é trabalho manual de horas.
- Com esta feature, 30 sticky notes viram 30 USs em 2 minutos, no formato exacto do board.
- Liga directamente os artefactos de discovery (Miro/Figma) aos artefactos de delivery (DevOps backlog).

**Como funciona tecnicamente:**
- `search_figma` e `search_miro` já existem e devolvem items/frames/boards.
- O fluxo seria: `search_miro` (buscar items do board) → para cada grupo lógico de items → `generate_user_stories` (gerar US com o conteúdo como contexto) → opcional: `create_workitem` (com confirmação).
- Não requer nova tool — requer que o agent saiba orquestrar esta sequência. Pode ser implementado como routing rule no system prompt + um prompt template para decomposição.

---

### FEATURE 7.D — "Before/After Diff US" (US automática a partir de comparação visual)

**O que é:**
A feature mais cirúrgica e imediata. O utilizador faz upload de exactamente 2 imagens — o "antes" e o "depois" — e o agente gera automaticamente uma US que descreve todas as diferenças visuais como ACs testáveis.

- *Upload: screenshot_actual.png + mockup_novo.png*
- *"Gera US com as diferenças"*

O agente identifica: novos elementos, elementos removidos, elementos que mudaram de posição/cor/estado/texto, e transforma cada diferença num AC específico e testável.

**Porque é game changer:**
- É o fluxo mais natural para qualquer melhoria UI: "está assim, quero que fique assim".
- Zero ambiguidade — as duas imagens são a especificação completa.
- O AC gerado é imediatamente testável pelo QA porque descreve o estado esperado de cada elemento.

**Como funciona tecnicamente:**
- Extensão da Feature 7.A. Em vez de uma imagem, o LLM recebe duas com instrução explícita de comparação.
- O `get_userstory_system_prompt()` já tem a regra de VISUAL PARSING. Basta adicionar um template de comparação: "Imagem 1 = ANTES, Imagem 2 = DEPOIS. Identifica TODAS as diferenças visuais e gera AC para cada uma."
- Requer suporte multimodal no LLM (já disponível em Claude Sonnet/Opus, não em GPT-4.1-mini).

---

### PRIORIZAÇÃO DAS FEATURES

| Feature | Esforço | Valor | Dependências | Prioridade |
|---------|---------|-------|--------------|------------|
| **7.A** Screenshot to US | Médio (3-5 dias) | Muito Alto | LLM multimodal activo (Anthropic quota) | **P1** |
| **7.D** Before/After Diff | Baixo (1-2 dias) | Alto | 7.A (é extensão) | **P1** (junto com 7.A) |
| **7.B** Context-Aware Refine | Baixo (2-3 dias) | Alto | Nenhuma | **P2** |
| **7.C** Batch from Figma/Miro | Médio (3-4 dias) | Alto | Figma/Miro tools funcionais com dados reais | **P3** |

**Constraint crítica:** As features 7.A e 7.D dependem de LLM multimodal. Hoje o tier "standard" usa Claude Sonnet (que suporta vision) mas via API directa que pode ter quota limitada. Se o Anthropic estiver em fallback para Azure OpenAI GPT-4.1-mini, vision pode não estar disponível. A feature deve degradar gracefully: se vision não disponível, pedir ao utilizador que descreva a imagem.

---

## PARTE 3 — ROADMAP ACTUALIZADO

```
CONCLUÍDO (v7.2.1)
─────────────────────────────────────
Fases 1A-5: Core, Security, Charts,
US Writer, Figma/Miro, Registry
Fase 6: PDF Chunking, Search Doc,
Daily Digest
Auditoria: 6 fixes críticos

PRÓXIMO — Fase 7.1 (Housekeeping)     Fase 7.2 (Game Changers)
─────────────────────────────────────  ─────────────────────────
12 findings não-críticos da auditoria  7.A Screenshot to US
Active→IsActive, CSV parser,           7.D Before/After Diff
memory caps, digest paralelo,          7.B Context-Aware Refine
/api/info split, cookie secure,        7.C Batch from Figma/Miro
logging cleanup
Bump → v7.3.0                          Bump → v8.0.0
```

**Decisão:** Fase 7.1 (housekeeping dos findings) e 7.2 (features) podem correr em paralelo — os findings são fixes cirúrgicos que não tocam no fluxo de USs.

---

— Claude (Arquiteto), 2026-02-24
