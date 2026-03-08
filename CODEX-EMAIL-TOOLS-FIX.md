# CODEX-EMAIL-TOOLS-FIX — Refactoring & Bug Fixes para Email/Tabular/Chart Tools

**Branch base:** `codex/email-tools`
**Objectivo:** Corrigir 7 issues identificados na code review, sem alterar comportamento funcional.
**Testes:** Todos os 233+ testes existentes devem continuar a passar. Adicionar testes novos onde indicado.

---

## Task 1 — Eliminar parsing duplicado em `tools_email.py` usando `tabular_loader`

### Problema
`_load_uploaded_email_table()` (linhas 256-334 de `tools_email.py`) duplica o parsing de CSV e XLSX que `tabular_loader.py` já faz (e melhor — suporta sniffing, xlsb, xls, tsv). O resultado é código duplicado que não suporta `.xlsb` nem `.tsv` para classificação de emails.

### O que fazer
1. Em `tools_email.py`, adicionar import:
   ```python
   from tabular_loader import load_tabular_dataset, TabularLoaderError
   ```
2. Reescrever `_load_uploaded_email_table()` para usar `load_tabular_dataset()` em vez do parsing inline:
   ```python
   async def _load_uploaded_email_table(conv_id: str, user_sub: str = "", filename: str = "") -> tuple[str, List[str], List[dict]]:
       safe_conv = str(conv_id or "").strip()
       safe_user = str(user_sub or "").strip()
       if not safe_conv:
           raise ValueError("conv_id é obrigatório para processar emails carregados.")

       safe_conv_odata = safe_conv.replace("'", "''")
       rows = await table_query("UploadIndex", f"PartitionKey eq '{safe_conv_odata}'", top=200)
       if not rows:
           raise ValueError("Não encontrei ficheiros carregados nesta conversa.")

       wanted = _normalize_header(filename)
       candidates = []
       for row in rows:
           owner_sub = str(row.get("UserSub", "") or "")
           if safe_user and owner_sub and owner_sub != safe_user:
               continue
           fname = str(row.get("Filename", "") or "")
           # AGORA aceita .xlsb e .tsv também
           if not fname.lower().endswith((".csv", ".tsv", ".xlsx", ".xls", ".xlsb")):
               continue
           raw_blob_ref = str(row.get("RawBlobRef", "") or "")
           if not raw_blob_ref:
               continue
           if wanted:
               norm = _normalize_header(fname)
               if wanted not in norm and norm != wanted:
                   continue
           candidates.append(row)

       if not candidates:
           raise ValueError("Não encontrei CSV/Excel adequado nesta conversa.")

       candidates.sort(key=lambda item: str(item.get("UploadedAt", "") or ""), reverse=True)
       selected = candidates[0]
       selected_name = str(selected.get("Filename", "") or "emails.xlsx")
       container, blob_name = parse_blob_ref(str(selected.get("RawBlobRef", "") or ""))
       if not container or not blob_name:
           raise ValueError("RawBlobRef inválido no ficheiro selecionado.")
       raw_bytes = await blob_download_bytes(container, blob_name)
       if not raw_bytes:
           raise ValueError("Ficheiro carregado vazio.")

       # Usar tabular_loader em vez de parsing inline
       try:
           dataset = load_tabular_dataset(raw_bytes, selected_name, max_rows=_EMAIL_UPLOAD_MAX_ROWS)
       except TabularLoaderError as exc:
           raise ValueError(str(exc))

       columns = list(dataset.get("columns") or [])
       records = list(dataset.get("records") or [])
       if not records:
           raise ValueError("Ficheiro sem linhas de dados.")
       return selected_name, columns, records
   ```
3. Remover os imports que já não são necessários APENAS se não forem usados noutro sítio do ficheiro:
   - Verificar se `csv`, `io` ainda são usados noutras funções (provavelmente sim em `_detect_csv_delimiter` e `_workbook_bytes_from_rows`) — se sim, manter.
   - Remover `import openpyxl` do bloco inline (era import local dentro da função).

### Ficheiros a alterar
- `tools_email.py`

### Testes
- O teste `test_classify_uploaded_emails_from_csv_generates_outlook_pack` já cobre CSV.
- O teste `test_classify_uploaded_emails_supports_legacy_messageinput_xlsx` já cobre XLSX.
- Ambos devem continuar a passar sem alteração (o monkeypatch de `blob_download_bytes` fornece os bytes, e `load_tabular_dataset` vai parsing igual).
- **Adicionar** um teste novo em `tests/test_tools_email.py`:
  ```python
  @pytest.mark.asyncio
  async def test_classify_uploaded_emails_accepts_tsv_files(monkeypatch):
      """Verifica que a classificação de emails aceita ficheiros .tsv."""
      capture = _DownloadCapture()
      monkeypatch.setattr(tools_email, "_store_generated_file", capture)

      async def fake_table_query(*args, **kwargs):
          return [{"Filename": "emails.tsv", "RawBlobRef": "container/blob.tsv", "UploadedAt": "2026-03-08T10:00:00+00:00"}]

      async def fake_blob_download_bytes(container, blob_name):
          return "EntryID\tSubject\tBody\nID-1\tTeste TSV\tConteúdo tab\n".encode("utf-8")

      async def fake_llm_simple(prompt, tier="standard", max_tokens=0, response_format=None):
          return json.dumps({"decisions": [{"row_id": "1", "label": "review", "confidence": 0.8, "reason": "TSV test.", "summary": "TSV email", "requires_manual_review": False}]})

      monkeypatch.setattr(tools_email, "table_query", fake_table_query)
      monkeypatch.setattr(tools_email, "blob_download_bytes", fake_blob_download_bytes)
      monkeypatch.setattr(tools_email, "llm_simple", fake_llm_simple)

      result = await tools_email.tool_classify_uploaded_emails(
          instructions="Classifica tudo como review.", conv_id="conv-tsv",
      )
      assert result["status"] == "ok"
      assert result["counts_by_label"] == {"review": 1}
  ```

---

## Task 2 — Adicionar `chart_uploaded_table` à injecção de `conv_id`/`user_sub` no `agent.py`

### Problema
Em `agent.py`, linhas 1323-1326, existe injecção de `conv_id` e `user_sub` para `classify_uploaded_emails` e `run_code`, mas **falta** para `chart_uploaded_table`. Sem isto, o chart tool recebe `conv_id=""` e falha a encontrar os ficheiros carregados.

### O que fazer
1. Procurar o bloco em `agent.py` onde está:
   ```python
   if tc.name == "classify_uploaded_emails" and not args.get("conv_id"):
       args["conv_id"] = conv_id
   if tc.name == "classify_uploaded_emails" and user_sub and not args.get("user_sub"):
       args["user_sub"] = user_sub
   ```
2. Adicionar logo a seguir (ou, melhor, refactorizar conforme Task 3):
   ```python
   if tc.name == "chart_uploaded_table" and not args.get("conv_id"):
       args["conv_id"] = conv_id
   if tc.name == "chart_uploaded_table" and user_sub and not args.get("user_sub"):
       args["user_sub"] = user_sub
   ```

### Ficheiros a alterar
- `agent.py`

### Testes
- Adicionar teste em `tests/test_agent_injection.py` (ou ficheiro existente de testes de agent, se houver) que verifica que `chart_uploaded_table` recebe `conv_id` do contexto. Se não existir ficheiro de testes de agent adequado, adicionar ao `tests/test_tools_email.py` ou criar `tests/test_agent_conv_injection.py`.

---

## Task 3 — Consolidar injecção de `conv_id`/`user_sub` num set

### Problema
O padrão de if/if para injectar `conv_id` e `user_sub` é copy-paste repetido para cada tool (`run_code`, `classify_uploaded_emails`, e agora `chart_uploaded_table`). Futuras tools vão precisar do mesmo.

### O que fazer
1. Em `agent.py`, definir um set no topo da secção relevante (perto dos imports ou antes do loop de tool calls):
   ```python
   _TOOLS_NEEDING_CONV_CONTEXT = {
       "run_code",
       "classify_uploaded_emails",
       "chart_uploaded_table",
       "analyze_uploaded_table",
   }
   ```
   (Incluir `analyze_uploaded_table` se já existir injecção para ele — verificar. Se não houver, não incluir.)

2. Substituir os blocos repetidos de if/if por:
   ```python
   if tc.name in _TOOLS_NEEDING_CONV_CONTEXT:
       if not args.get("conv_id"):
           args["conv_id"] = conv_id
       if user_sub and not args.get("user_sub"):
           args["user_sub"] = user_sub
   ```

3. Manter o bloco especial de `run_code` que faz `args["conv_id"] = conv_id` (sem o `if not args.get(...)` guard) se esse comportamento for intencional — verificar se `run_code` precisa de override forçado.

### Ficheiros a alterar
- `agent.py`

### Testes
- Os testes existentes de `classify_uploaded_emails` devem continuar a passar.

---

## Task 4 — Validar que colunas inferidas existem no dataset (chart spec)

### Problema
`_build_uploaded_table_chart_spec()` em `tools.py` infere `x_column`, `y_column` e `series_column` a partir do preview, mas **nunca valida que as colunas inferidas realmente existem na lista `columns`**. Se a inferência falhar ou devolver uma coluna inexistente, o código do code interpreter vai crashar com `KeyError` em runtime.

### O que fazer
1. No final de `_build_uploaded_table_chart_spec()`, antes do `return`, adicionar validação:
   ```python
   # Validar que as colunas inferidas existem no dataset
   col_set = set(columns)
   if resolved_x and resolved_x not in col_set:
       resolved_x = ""
   if resolved_y and resolved_y not in col_set:
       resolved_y = ""
   if resolved_series and resolved_series not in col_set:
       resolved_series = ""
   ```
   Isto deve ser adicionado ANTES da atribuição de `x_kind` (que usa `resolved_x`).

2. Adicionar teste em `tests/camada_b_tools/test_tabular_charting.py`:
   ```python
   def test_chart_spec_clears_nonexistent_columns(self):
       from tools import _build_uploaded_table_chart_spec

       preview = {
           "columns": ["Date", "Revenue"],
           "sample_records": [{"Date": "2026-01-01", "Revenue": "10"}],
           "column_types": {"Date": "date", "Revenue": "numeric"},
           "row_count": 1,
       }
       spec = _build_uploaded_table_chart_spec(
           "gráfico de barras",
           preview,
           chart_type="bar",
           x_column="ColunaNaoExiste",
           y_column="OutraInexistente",
       )
       # Colunas inexistentes devem ser limpas (string vazia) em vez de mantidas
       assert spec["x_column"] != "ColunaNaoExiste"
       assert spec["y_column"] != "OutraInexistente"
   ```

### Ficheiros a alterar
- `tools.py`
- `tests/camada_b_tools/test_tabular_charting.py`

---

## Task 5 — Mover chart code template de f-string para JSON spec injection

### Problema
`_build_uploaded_table_chart_code()` em `tools.py` (linhas ~1347-1689) constrói ~340 linhas de Python como f-string com `{{` escape para cada `{` do template. Isto é frágil, difícil de manter, e qualquer erro de escape causa bugs silenciosos.

### O que fazer
1. O template JÁ usa `json.loads(payload_json)` para receber o spec — o que está bem. O problema é que o CORPO do template tem muitos `{{` desnecessários porque está embutido como f-string.

2. Refactorizar para usar string concatenation ou `.format()` apenas para a injecção do payload:
   ```python
   def _build_uploaded_table_chart_code(filename: str, spec: dict, query: str) -> str:
       payload = {
           "filename": filename,
           "spec": spec,
           "query": query,
       }
       payload_json = json.dumps(payload, ensure_ascii=False)
       # Usar template estático + substituição controlada apenas do payload
       return _CHART_CODE_TEMPLATE.replace("__PAYLOAD_JSON__", repr(payload_json))
   ```

3. Extrair o template para uma constante string (com `{` normais, sem escapes):
   ```python
   _CHART_CODE_TEMPLATE = r'''
   import json
   import math
   from pathlib import Path
   import pandas as pd

   payload = json.loads(__PAYLOAD_JSON__)
   spec = payload["spec"]
   query = payload.get("query", "")
   filename = payload["filename"]
   # ... resto do template com { e } normais ...
   '''.strip()
   ```

4. ATENÇÃO: Este refactoring é delicado. Garantir que o resultado final é EXACTAMENTE o mesmo código Python que seria enviado ao code interpreter. Testar com o teste existente `test_chart_uploaded_table_generates_artifacts`.

### Ficheiros a alterar
- `tools.py`

### Testes
- O teste `test_chart_uploaded_table_generates_artifacts` em `tests/camada_b_tools/test_tabular_charting.py` deve continuar a passar sem alteração.
- **Adicionar** um teste unitário para `_build_uploaded_table_chart_code`:
  ```python
  def test_chart_code_template_produces_valid_python(self):
      from tools import _build_uploaded_table_chart_code
      spec = {
          "chart_type": "bar",
          "x_column": "Category",
          "y_column": "Revenue",
          "series_column": "",
          "agg": "sum",
          "top_n": 20,
          "max_points": 2000,
          "x_kind": "",
      }
      code = _build_uploaded_table_chart_code("sample.csv", spec, "test query")
      # O código gerado deve ser Python válido (compilável)
      compile(code, "<chart_template>", "exec")
      # E deve conter a referência ao ficheiro
      assert "sample.csv" in code
  ```

---

## Task 6 — Evitar carregar raw bytes completos no dict de `_resolve_uploaded_tabular_source`

### Problema
`_resolve_uploaded_tabular_source()` descarrega os bytes completos do blob e coloca-os num dict (`{"raw_bytes": raw_bytes, ...}`). Para ficheiros grandes (até 40MB CSV), isto retém os bytes em memória por mais tempo que o necessário porque o dict é passado como valor de retorno e só depois é consumido.

### O que fazer
Este é um refactoring de LOW priority. A solução mínima é documentar o padrão:
1. Adicionar um comentário no retorno de `_resolve_uploaded_tabular_source()` a explicar que `raw_bytes` é potencialmente grande e deve ser consumido imediatamente pelo caller:
   ```python
   # NOTA: raw_bytes pode ser grande (até 40MB). O caller deve processar
   # e libertar a referência o mais rápido possível.
   return {
       "filename": selected_filename,
       "raw_bytes": raw_bytes,
       "upload_row": selected,
   }
   ```

2. Nos callers (`tool_analyze_uploaded_table` e `tool_chart_uploaded_table`), após usar `raw_bytes`, fazer `del` explícito:
   ```python
   raw_bytes = source.get("raw_bytes") or b""
   # ... usar raw_bytes para load_tabular_dataset/preview ...
   del source  # libertar referência ao dict com bytes grandes
   ```

### Ficheiros a alterar
- `tools.py`

### Testes
- Testes existentes devem continuar a passar.

---

## Task 7 — Remover `_detect_csv_delimiter` de `tools_email.py`

### Problema
`_detect_csv_delimiter()` em `tools_email.py` (linhas 92-98) é agora dead code após Task 1, porque o parsing CSV passa a ser feito por `tabular_loader.py` (que tem a sua própria lógica de sniffing).

### O que fazer
1. Após implementar Task 1, verificar se `_detect_csv_delimiter` ainda é usado em algum sítio de `tools_email.py`.
2. Se não for usado, remover a função.
3. Verificar imports (`csv`, `io`) — remover se já não forem usados noutro sítio do ficheiro. CUIDADO: `csv` é provavelmente usado em `_workbook_bytes_from_rows` ou noutras funções — verificar antes de remover.

### Ficheiros a alterar
- `tools_email.py`

### Testes
- Testes existentes devem continuar a passar.

---

## Resumo de Prioridades

| Task | Prioridade | Risco | Ficheiros |
|------|-----------|-------|-----------|
| 2 — conv_id injection para chart | **ALTA** | Bug real (chart não funciona em prod) | `agent.py` |
| 4 — Validação colunas no chart spec | **ALTA** | Bug real (crash no code interpreter) | `tools.py`, tests |
| 1 — Deduplicate parsing com tabular_loader | **MÉDIA** | Code quality + suporte xlsb/tsv | `tools_email.py`, tests |
| 3 — Consolidar conv_id injection set | **MÉDIA** | Maintainability | `agent.py` |
| 7 — Remover dead code | **BAIXA** | Cleanup (depende de Task 1) | `tools_email.py` |
| 5 — Chart template de f-string | **BAIXA** | Maintainability (risco de regressão) | `tools.py`, tests |
| 6 — Comentar/del raw_bytes | **BAIXA** | Documentação | `tools.py` |

## Ordem de execução recomendada
1. Task 2 (bug fix rápido, 2 linhas)
2. Task 3 (refactoring do que Task 2 acabou de adicionar)
3. Task 4 (bug fix, ~5 linhas)
4. Task 1 (refactoring médio)
5. Task 7 (cleanup, depende de Task 1)
6. Task 6 (comentários)
7. Task 5 (refactoring grande, fazer por último por risco de regressão)

## Validação Final
```bash
python -m pytest tests/ -x -q
```
Todos os testes devem passar. O número total de testes deve ser >= 235 (233 existentes + pelo menos 2 novos).
