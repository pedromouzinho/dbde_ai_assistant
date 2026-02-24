# DBDE AI Assistant — Manual de Utilizador v7.1.1

> **Millennium BCP | Equipa DBDE (DIT/ADMChannels)**
>
> **Nota de apresentação:** texto corrido a preto; títulos e destaques em cerise (`#DE3163`).
>
> **Documento de referência:** esta versão substitui operacionalmente o manual `v7.0.2` e incorpora as evoluções até `v7.1.1`.

---

## 1. Objetivo

O DBDE AI Assistant é um assistente interno para acelerar o trabalho da equipa DBDE no Azure DevOps e na documentação MSE, com suporte a:

- Perguntas em linguagem natural sobre backlog e work items.
- Pesquisa semântica em DevOps e conteúdos internos.
- Geração e refinação de User Stories.
- Criação controlada de work items no board.
- Exportação de resultados (CSV, XLSX, PDF, HTML).
- Visualização de dados em gráficos interativos.

---

## 2. Acesso e autenticação

1. Aceder a `https://dbdeai.pt`.
2. Iniciar sessão com credenciais internas.
3. Escolher o modo de utilização:
- `General` para análise geral e suporte operacional.
- `Userstory` para ciclo de escrita orientado a Draft → Review → Final.

---

## 3. Utilização diária

### 3.1 Perguntas gerais

Exemplos:
- “Quantas USs ativas existem?”
- “Mostra as 10 USs mais recentes em tabela.”
- “Resume este texto.”

### 3.2 Escrita de User Stories

No modo `Userstory`, o assistente segue ciclo iterativo:
1. Gera rascunho (Draft).
2. Recolhe feedback.
3. Entrega versão final refinada.

### 3.3 Criação de work items

A criação no DevOps exige confirmação explícita do utilizador.
O assistente só executa após confirmação inequívoca (ex.: “confirmo”, “sim, avança”).

---

## 4. Ferramentas disponíveis (produção)

1. `query_workitems`
2. `search_workitems`
3. `search_website`
4. `analyze_patterns`
5. `generate_user_stories`
6. `query_hierarchy`
7. `compute_kpi`
8. `create_workitem`
9. `generate_chart`
10. `generate_file`

---

## 5. Upload de ficheiros

Formatos suportados para análise:
- `.xlsx`, `.xls`, `.csv`, `.txt`, `.pdf`, `.svg`

Comportamento:
- O ficheiro é lido e injetado no contexto da conversa.
- Em ficheiros tabulares, é feita análise de colunas (`numeric/text`) para apoiar gráficos.
- Em modo `userstory`, o pré-processamento melhora a extração de requisitos.

Estado atual de `.pptx`:
- Implementação técnica existe, mas o suporte está temporariamente em backlog técnico (`shelved/TBD`) por tema de runtime/dependências.

---

## 6. Gráficos e exportação

### 6.1 Gráficos

Quando houver dados estruturados, o assistente pode gerar gráficos interativos (bar, pie, line, scatter, histogram, hbar).

### 6.2 Exportação

Formatos disponíveis:
- `CSV`
- `XLSX`
- `PDF`
- `HTML`

Os botões de exportação aparecem junto às respostas com dados exportáveis.

---

## 7. Boas práticas

- Pedir primeiro uma contagem/KPI e depois detalhar em tabela.
- Para exportação, pedir explicitamente “em tabela”.
- Para criar work item, validar sempre título, área, assignee e tags antes de confirmar.
- No modo `Userstory`, dar feedback objetivo para melhorar iterações.

---

## 8. Limitações conhecidas

- O suporte `.pptx` está adiado até estabilização definitiva do runtime no App Service.
- Alguns pedidos muito amplos podem exigir refinamento em 2-3 iterações.
- Em caso de falha de serviço externo (DevOps/Search), o sistema aplica fallback e regista logs.

---

## 9. Estado da versão

- **Produção atual:** `v7.1.1`
- **Manual anterior:** `DBDE_AI_Manual_Utilizador_v7.0.2.pdf` (histórico)
- **Próxima fase prevista:** `v7.2.0` (integrações e polish)

---

## 10. Contactos operacionais

- Product Owner: Pedro Mousinho
- Equipa: DBDE | DIT/ADMChannels | Millennium BCP
