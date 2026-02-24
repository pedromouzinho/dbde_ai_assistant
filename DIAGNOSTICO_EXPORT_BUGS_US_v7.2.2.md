# Diagnóstico e Solução — Export Bugs/User Stories (v7.2.2)

Data: 2026-02-24

## Sintoma reportado
- Pedido do utilizador: bugs da Epic `636120` + user stories da Feature `723810`, sem Features.
- Resposta mostrada no chat estava correta.
- Export (CSV/XLSX/PDF/HTML) trazia registos fora do critério, incluindo `Feature`.
- PDF ilegível (colunas com texto comprido sem quebra adequada).

## Evidência observada
- Ficheiro: `/Users/pedromousinho/Downloads/Vai_me_buscar_todos_os_bugs_de.csv`
- Conteúdo exportado inclui `Type = Feature` (ex.: IDs `552330`, `552336`) e coluna `Parent Id`, logo o dataset exportado não correspondeu ao subconjunto esperado pelo utilizador.

## Causa raiz
1. Seleção de dataset errado no frontend
- O frontend escolhia sempre o **primeiro** payload exportável da mensagem.
- Em interações com várias tool calls, esse primeiro payload podia ser um resultado intermédio/amplo (com Features), enquanto o texto final mostrava um subconjunto filtrado.
- Referência anterior: `getFirstExportableData(...)` em `static/index.html`.

2. Ambiguidade no índice exportável no fluxo sync/stream
- Apesar de existir `export_index` no backend, não era usado no frontend para escolher o dataset a exportar.
- No sync, o cálculo de `export_index` também podia apontar para índice errado quando havia múltiplas tool calls no mesmo batch.

3. PDF com layout tabular frágil
- Renderização PDF usava `cell(...)` por coluna sem quebra de texto por célula.
- Títulos/URLs longos ficavam truncados, comprimindo legibilidade.

## Solução aplicada

### A) Export passa a usar dataset correto
- Frontend agora usa `export_index` quando disponível, e fallback para **último** payload exportável (não o primeiro).
- Alterações em `static/index.html`:
  - `getPreferredExportableData(toolResults, preferredIndex)`
  - `messageHasExportableData(...)` usa `message.export_index`
  - `exportData(...)` e `exportMessageData(...)` usam seleção preferencial
  - Botão export da mensagem passa `message.export_index`
  - Fluxo sync/stream persiste `export_index` na mensagem

### B) Backend stream/sync passa `export_index` consistente
- `agent.py`:
  - Sync: cálculo correto do índice exportável por posição real do `tool_details` no batch
  - Stream: evento `done` inclui `has_exportable_data` e `export_index`

### C) PDF legível
- `export_engine.py`:
  - Novas funções para largura ponderada por coluna e wrap de texto por célula
  - Repetição de header em nova página
  - Zebra rows mantidas
  - Melhor leitura para colunas longas (`title`, `url`, `area`)

## Hardening adicional incluído no v7.2.2
- `JWT_SECRET` obrigatório em produção (`config.py`), com fail-fast se ausente.
- Rate limiting adicionado em endpoints sensíveis de auth/feedback/chats/learning (`app.py`).
- Escape/encoding defensivo de `PartitionKey`/`RowKey` em `table_merge` e `table_delete` (`storage.py`).

## Validação recomendada pós-deploy
1. Repetir o mesmo pedido no chat.
2. Exportar `CSV/XLSX/PDF/HTML` da resposta.
3. Confirmar:
- Não existem `Feature` quando o pedido exclui Features.
- PDF mantém colunas legíveis com quebra de texto.
- `/api/info` devolve `version = 7.2.2`.

## Validação executada em produção (2026-02-24)
- Endpoint base validado: `https://millennium-ai-assistant-epa7d7b4defabwbn.swedencentral-01.azurewebsites.net`
- `GET /api/info`: `version = 7.2.2`
- `GET /api/runtime/check` (admin): `manifest_all_match = true`
- Teste E2E com prompt do caso real (bugs + US, sem features, com área):
  - tool acionada: `query_hierarchy`
  - dataset exportado: `30` itens
  - tipos: `Bug=30`, `Feature=0`
  - itens fora de `RevampFEE MVP2`: `0`
- Exports gerados com sucesso:
  - `/Users/pedromousinho/Downloads/E2E_Bugs_US_v722.csv`
  - `/Users/pedromousinho/Downloads/E2E_Bugs_US_v722.xlsx`
  - `/Users/pedromousinho/Downloads/E2E_Bugs_US_v722.pdf`
  - `/Users/pedromousinho/Downloads/E2E_Bugs_US_v722.html`
