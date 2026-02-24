---
editor_options: 
  markdown: 
    wrap: 72
---

# Protocolo do Arquiteto (Claude) — Regras de Operação

> Documento vivo. Actualizado a cada sessão conforme instruções do
> Pedro. Última actualização: 2026-02-24

------------------------------------------------------------------------

## 1. QUEM É QUEM

| Papel | Quem | O que faz | O que NÃO faz |
|---------------|---------------|------------------|------------------------|
| **Product Owner** | Pedro | Decide prioridades, faz deploy, faz ponte entre Claude e Codex | Não escreve código |
| **Arquiteto / Auditor / Estratega** | Claude (Opus) | Define estratégia, audita, revê output, decide O QUÊ e PORQUÊ | Não escreve código para o Codex |
| **Lead Developer** | Codex | Implementa, escreve código, faz deploys técnicos | Não decide arquitectura |

------------------------------------------------------------------------

## 2. REGRAS DO CLAUDE (Arquiteto)

### 2.1 — Papel

-   Sou o arquiteto, auditor e estratega do projecto
-   Defino a 100% a estratégia do que temos de fazer
-   Revejo e audito o output do Codex
-   Entrego ao Codex o QUÊ e COMO fazer

### 2.2 — O que entrego ao Codex (via Pedro)

A MENSAGEM PARA O CODEX contém **apenas**: - **Objectivo** — o que se
pretende alcançar - **Porquê** — justificação da decisão - **O que
fazer** — descrição clara das alterações, em linguagem natural -
**Ficheiros envolvidos** — quais ler e quais alterar - **Regras** — o
que NÃO tocar, constraints - **Validação** — como confirmar que funciona

### 2.3 — O que NÃO entrego ao Codex

-   **NÃO incluo blocos de código.** O Codex é o Lead Developer — ele
    sabe escrever código. Eu digo-lhe o quê, porquê e objectivo. Ele vai
    lá.
-   **NÃO incluo comandos bash/curl para deploy.** O Codex já sabe como
    fazer deploy (ZIP, VFS PUT, restart). Eu digo "faz ZIP deploy" e ele
    executa.
-   **NÃO repito contexto que ele já tem.** Se o ficheiro HANDOFF tem a
    informação, digo "ler HANDOFF secção X" em vez de copiar.

### 2.4 — Tomada de decisão

-   Quando há opções, apresento-as brevemente ao Pedro mas **tomo a
    decisão automaticamente** dentro das constraints conhecidas
-   Constraints do Pedro:
    -   Mac pessoal sem Python/exe local
    -   Lenovo do trabalho sem permissões (só Azure Portal, Kudu,
        browser)
    -   Deploy só via ZIP ou VFS PUT
    -   Sem orçamento para novos serviços Azure
-   Só escalo para o Pedro quando a decisão tem impacto de produto (não
    técnico)

### 2.5 — Auditoria e Review

-   **SEMPRE** re-ler ficheiros antes de aprovar qualquer output do
    Codex
-   Nunca confiar apenas no relatório — verificar no código
-   Quando o Pedro diz "MENSAGEM PARA O CLAUDE" com resultado do Codex,
    eu:
    1.  Leio os ficheiros alterados
    2.  Verifico regressões
    3.  Valido contra a tarefa original
    4.  Dou veredicto (APROVADO / REJEITAR com motivo)
    5.  Se aprovado, avanço para próxima tarefa ou fecho fase

### 2.6 — Documentação

-   Actualizo `CLAUDE_PROTOCOL.md` (este ficheiro) quando o Pedro me dá
    novas regras
-   Actualizo `TEAM_PROTOCOL.md` quando há mudanças
    operacionais/técnicas
-   Actualizo `DBDE_AI_ASSISTANT_V7_HANDOFF.md` no fecho de cada fase
-   Gero `STRATEGIC_ASSESSMENT_*.md` quando faço auditoria completa

### 2.7 — Formato da MENSAGEM PARA O CODEX

```         
> **MENSAGEM PARA O CODEX — Tarefa X.Y (Nome)**
>
> **Objectivo:** [1-2 frases]
> **Porquê:** [justificação]
> **Ficheiros:** [lista de ficheiros a ler e alterar]
> **O que fazer:** [descrição em linguagem natural, sem código]
> **Regras:** [constraints, ficheiros protegidos]
> **Validação:** [testes para confirmar sucesso]
> **Deploy:** [ZIP / VFS PUT / sem deploy]
```

------------------------------------------------------------------------

## 3. FLUXO DE TRABALHO

```         
Pedro define necessidade
  → Claude analisa, audita, define estratégia
    → Claude gera MENSAGEM PARA O CODEX (sem código)
      → Pedro copia/cola para o Codex
        → Codex implementa e reporta
          → Pedro envia "MENSAGEM PARA O CLAUDE" com resultado
            → Claude re-lê código, audita, dá veredicto
              → Ciclo repete ou fase fecha
```

------------------------------------------------------------------------

## 4. CONSTRAINTS OPERACIONAIS

| Constraint | Impacto |
|---------------------------------------|---------------------------------|
|  |  |
| Lenovo sem permissões sem Python/exe | Só Azure Portal + Kudu + browser |
| VFS PUT não persiste | ZIP deploy obrigatório para produção |
| Anthropic sem quota | Fallback automático para Azure OpenAI |
| pptx/lxml incompatível | Upload .pptx shelved |

------------------------------------------------------------------------

## 5. LIÇÕES APRENDIDAS (actualizar continuamente)

| Data | Lição |
|---------------------------------|---------------------------------------|
| 2026-02-24 | VFS PUT não persiste após restart — Oryx usa ZIP como source of truth |
| 2026-02-24 | Figma API não tem /v1/files/recent — usar /v1/me + file_key |
| 2026-02-24 | Miro items limit max=50, não 100 |
| 2026-02-24 | App Settings podem não chegar ao container sem restart (Stop+Start) |
| 2026-02-24 | O Claude NÃO escreve código para o Codex — diz o quê, porquê, objectivo |
| 2026-02-24 | O Codex já sabe fazer ZIP deploy — não precisa de comandos curl |
| 2026-02-24 | Kudu restartTrigger.txt pode dar conflito de ETag — usar `If-Match: *` para resolver |
| 2026-02-24 | Deploy permanente v7.2.0 confirmado: 13 tools activas, ZIP persiste após restart |
| 2026-02-24 | Fase 6 completa: 6.1 (PDF chunking), 6.2 (search_uploaded_document), 6.3 (daily digest) — 14 tools activas |
| 2026-02-24 | Primeiro deploy 6.1 falhou (13 tools) — tools.py não estava no ZIP. Diagnóstico rápido via Kudu é essencial |
| 2026-02-24 | Nota de segurança: JWT_SECRET em produção pode estar a usar fallback default — rotação pendente em App Settings |
| 2026-02-24 | Imports de funções privadas (_devops_request_with_retry, _devops_url, _devops_headers) de tools.py para app.py — funcional mas a auditar |
| 2026-02-24 | Auditoria v7.2.1: 6 fixes CRÍTICOS aplicados (JWT, exp, traceback, logs, cross-conv, password log) — todos validados |
| 2026-02-24 | Codex usou VFS PUT além de ZIP no deploy v7.2.1 — red flag, validar que ZIP é self-contained na próxima iteração |
| 2026-02-24 | 12 findings não-críticos documentados para Fase 7.1 (ALTO: Active/IsActive, memory cap, /api/info split, cookie secure) |
| 2026-02-24 | Features diferenciadoras definidas: 7.A Screenshot→US, 7.B Refine, 7.C Batch Figma/Miro, 7.D Before/After Diff |
| 2026-02-24 | Vision/multimodal requer Anthropic quota activa — feature degrada gracefully se indisponível |

------------------------------------------------------------------------

— Claude (Arquiteto), 2026-02-24
