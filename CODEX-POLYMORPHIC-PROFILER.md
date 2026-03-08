# CODEX-POLYMORPHIC-PROFILER — Smart Schema Profiler + Data Dictionary

**Branch base:** `main` (latest)
**Objectivo:** Permitir ao AI assistant analisar ficheiros tabulares com schema polimórfico
(colunas genéricas como `campo_1..campo_30` cujo significado muda conforme um pivot como `transaction_Id`).
**Testes:** Todos os 245+ testes existentes devem continuar a passar. Adicionar ~15 testes novos.

---

## Contexto do Problema

Ficheiros vindos de SQL Server do banco têm este padrão:
- 64 colunas, ~30 sempre vazias
- Colunas genéricas `campo_0` a `campo_30` cujo significado depende do valor de `transaction_Id`
- Ex: para tx=871, `campo_1` é session UUID; para tx=2066, `campo_1` é dados encriptados base64
- 18 `transaction_Id` distintos, cada um é essencialmente uma "tabela virtual" diferente

O AI precisa de:
1. Detectar automaticamente o padrão polimórfico no upload
2. Gerar um perfil inteligente que segmenta por pivot
3. Guardar e reutilizar contexto de negócio que o utilizador vai fornecendo ao longo do tempo

---

## Task 1 — Detecção de padrão polimórfico em `tabular_loader.py`

### O que fazer

Adicionar uma nova função `detect_polymorphic_schema()` em `tabular_loader.py` que analisa
um preview tabular e detecta se o dataset tem padrão EAV/polimórfico.

**Heurísticas de detecção:**

1. **Colunas genéricas**: 3+ colunas cujo nome segue padrões como `campo_N`, `field_N`,
   `col_N`, `column_N`, `attr_N`, `param_N`, `value_N` (regex: `^(campo|field|col|column|attr|param|value|dado|data|var|prop)[_\s]?\d+$`)
2. **Coluna pivot candidata**: Coluna com poucos valores distintos (2-50) e 90%+ fill rate,
   onde os campos genéricos mudam de fill rate/tipo entre os valores do pivot
3. **Muitas colunas vazias**: 30%+ das colunas com 0% fill rate

```python
# Constantes
_GENERIC_COLUMN_PATTERN = re.compile(
    r"^(campo|field|col|column|attr|param|value|dado|data|var|prop)[_\s]?\d+$",
    re.IGNORECASE,
)
_POLYMORPHIC_GENERIC_THRESHOLD = 3       # mínimo de colunas genéricas
_POLYMORPHIC_PIVOT_MAX_DISTINCT = 50     # máximo de valores distintos no pivot
_POLYMORPHIC_PIVOT_MIN_FILL = 0.90       # fill rate mínimo do pivot
_POLYMORPHIC_EMPTY_COL_THRESHOLD = 0.30  # 30% colunas vazias → sinal polimórfico


def detect_polymorphic_schema(
    columns: list[str],
    sample_records: list[dict],
    column_types: dict[str, str],
    row_count: int,
) -> dict | None:
    """
    Detecta se o dataset tem schema polimórfico (EAV).

    Retorna None se não detectar padrão, ou um dict com:
    {
        "is_polymorphic": True,
        "pivot_column": "transaction_Id",
        "generic_columns": ["campo_1", "campo_2", ...],
        "empty_columns": ["CONTA_DO_COID", ...],
        "universal_columns": ["HC_ID", "DATA_HORA_I_trans", ...],
        "pivot_profiles": {
            "871": {
                "row_count": 120,
                "filled_generics": {"campo_1": {"fill_pct": 100, "inferred_type": "uuid", "samples": [...]}, ...},
                "empty_generics": ["campo_5", "campo_6", ...]
            },
            ...
        },
        "summary_text": "Dataset polimórfico detectado. Pivô: transaction_Id (18 tipos)..."
    }
    """
```

**Lógica:**
1. Identificar colunas genéricas via regex
2. Se < 3 genéricas, return None
3. Para cada coluna não-genérica com 2-50 valores distintos e 90%+ fill:
   - Agrupar sample_records por valor dessa coluna
   - Para cada grupo: calcular fill rate de cada coluna genérica
   - Se pelo menos 2 grupos tiverem patterns diferentes de fill nos genéricos → é pivot
   - Escolher a coluna com mais variação inter-grupo como pivot
4. Se pivot encontrado, gerar perfil por grupo:
   - Fill rate de cada genérico no grupo
   - Tipo inferido (UUID se padrão `[a-f0-9-]{36}`, date se parseable, numeric, base64 se `[A-Za-z0-9+/=]{40,}`, text)
   - 2-3 samples (truncados a 60 chars)
5. Identificar colunas "universais" (90%+ fill em TODOS os grupos)
6. Identificar colunas "vazias" (0% fill global)
7. Gerar `summary_text` legível para injecção no prompt

**Função auxiliar para tipo refinado:**
```python
def _infer_generic_value_type(values: list[str]) -> str:
    """Infere tipo mais específico que text/numeric/date.
    Retorna: 'uuid', 'numeric', 'date', 'boolean', 'base64_encoded', 'fixed_value', 'text'
    """
```

### Ficheiros a alterar
- `tabular_loader.py`

### Testes (em `tests/test_polymorphic_profiler.py`)
```python
class TestPolymorphicDetection:
    def test_detects_polymorphic_pattern_with_campo_columns(self):
        # Simular dataset tipo Tbl_Contact_Detail
        ...

    def test_returns_none_for_clean_dataset(self):
        # Dataset normal sem campos genéricos
        ...

    def test_identifies_correct_pivot_column(self):
        # Dataset com transaction_Id como pivot
        ...

    def test_generates_per_pivot_profiles(self):
        # Verificar que cada valor do pivot tem fill rates correctos
        ...

    def test_detects_empty_columns(self):
        # Verificar que colunas 0% fill são listadas
        ...

    def test_infers_uuid_type(self):
        ...

    def test_infers_base64_type(self):
        ...
```

---

## Task 2 — Integrar detecção polimórfica no upload pipeline

### O que fazer

Em `app.py`, na função `_extract_upload_entry()`, após chamar `load_tabular_preview()`,
invocar `detect_polymorphic_schema()` e guardar o resultado no `store_entry` e no `UploadIndex`.

1. Adicionar import:
   ```python
   from tabular_loader import detect_polymorphic_schema
   ```

2. Após a linha `col_analysis = list(preview.get("col_analysis") or [])` (~linha 1795), adicionar:
   ```python
   poly_schema = detect_polymorphic_schema(
       col_names,
       list(preview.get("sample_records") or []),
       dict(preview.get("column_types") or {}),
       row_count,
   )
   ```

3. No `store_entry` (que é guardado em memória no `uploaded_files_store`), adicionar:
   ```python
   if poly_schema:
       store_entry["polymorphic_schema"] = poly_schema
   ```

4. No entity que vai para o `UploadIndex` Azure Table, guardar o `summary_text` como campo:
   ```python
   if poly_schema:
       entity["PolymorphicSummary"] = poly_schema.get("summary_text", "")[:32000]
       entity["PivotColumn"] = poly_schema.get("pivot_column", "")
   ```

5. No `result_payload` que é devolvido ao frontend, incluir indicação:
   ```python
   if poly_schema:
       result_payload["polymorphic"] = True
       result_payload["pivot_column"] = poly_schema.get("pivot_column", "")
       result_payload["pivot_values_count"] = len(poly_schema.get("pivot_profiles", {}))
   ```

### Ficheiros a alterar
- `app.py`

### Testes
- O teste `test_extract_upload_entry_accepts_xlsb` em `test_tabular_charting.py` deve continuar a passar.
- Adicionar teste que verifica que `_extract_upload_entry` com dados polimórficos devolve `polymorphic_schema`.

---

## Task 3 — Injectar contexto polimórfico no system prompt do agent

### O que fazer

Quando o utilizador tem ficheiros carregados com schema polimórfico, o AI precisa de receber
essa informação no prompt para poder raciocinar correctamente.

1. Em `agent.py`, na função `_inject_file_context()`, verificar se algum ficheiro tem
   `polymorphic_schema`:

   ```python
   poly_schemas = []
   for file_info in files:
       poly = file_info.get("polymorphic_schema")
       if poly and poly.get("is_polymorphic"):
           poly_schemas.append({
               "filename": file_info.get("filename", ""),
               "summary": poly.get("summary_text", ""),
               "pivot_column": poly.get("pivot_column", ""),
           })
   ```

2. Se houver schemas polimórficos, adicionar bloco ao contexto:
   ```python
   if poly_schemas:
       poly_block = "\n\n## DATASETS POLIMÓRFICOS DETECTADOS\n"
       poly_block += "ATENÇÃO: Os ficheiros abaixo têm colunas genéricas (campo_N) cujo significado "
       poly_block += "muda conforme a coluna pivot. NÃO misturar dados de pivot values diferentes.\n"
       poly_block += "Para cada análise: 1) Filtrar por pivot value 2) Interpretar campos conforme o perfil desse valor.\n"
       for ps in poly_schemas:
           poly_block += f"\n### {ps['filename']} (pivô: {ps['pivot_column']})\n"
           poly_block += ps["summary"] + "\n"
       # Adicionar ao contexto do ficheiro
   ```

3. Também em `_ensure_uploaded_files_loaded()`, quando carrega do `UploadIndex`, verificar
   se existe `PolymorphicSummary` e reconstruir o campo `polymorphic_schema` no `file_info`:
   ```python
   poly_summary = str(row.get("PolymorphicSummary", "") or "")
   pivot_col = str(row.get("PivotColumn", "") or "")
   if poly_summary:
       file_entry["polymorphic_schema"] = {
           "is_polymorphic": True,
           "summary_text": poly_summary,
           "pivot_column": pivot_col,
       }
   ```

### Ficheiros a alterar
- `agent.py`

### Testes
- Teste unitário que verifica que `_inject_file_context` inclui bloco polimórfico quando presente.

---

## Task 4 — Data Dictionary storage e tools

### O que fazer

Criar `data_dictionary.py` com funções para guardar/ler mapeamentos de negócio na Azure Table.

**Schema da Azure Table `DataDictionary`:**
- `PartitionKey`: nome da tabela normalizado (ex: `tbl_contact_detail`)
- `RowKey`: chave composta `{pivot_value}::{column_name}` (ex: `871::campo_1`) ou
  `__global__::{column_name}` para mapeamentos que se aplicam a todos os pivot values
  (ex: `__global__::channel_Id`)
- Campos:
  - `PivotColumn`: nome da coluna pivot (ex: `transaction_Id`)
  - `PivotValue`: valor do pivot (ex: `871`) ou `__global__`
  - `ColumnName`: nome original da coluna (ex: `campo_1`)
  - `MappedName`: nome de negócio (ex: `session_id`)
  - `Description`: descrição livre (ex: `UUID da sessão do utilizador na app mobile`)
  - `DataType`: tipo (ex: `uuid`, `numeric`, `date`, `text`)
  - `UpdatedAt`: timestamp ISO
  - `UpdatedBy`: user_sub

```python
# data_dictionary.py

import logging
import re
from datetime import datetime, timezone
from storage import table_insert, table_merge, table_query

logger = logging.getLogger(__name__)

_TABLE_NAME = "DataDictionary"


def _normalize_table_name(name: str) -> str:
    """Normaliza nome da tabela: lowercase, remove extensão."""
    import os
    base = os.path.splitext(str(name or "").strip())[0]
    return re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or "unknown"


def _make_row_key(pivot_value: str, column_name: str) -> str:
    safe_pivot = str(pivot_value or "__global__").strip()
    safe_col = str(column_name or "").strip()
    return f"{safe_pivot}::{safe_col}"


async def save_mapping(
    table_name: str,
    pivot_column: str,
    pivot_value: str,
    column_name: str,
    mapped_name: str,
    description: str = "",
    data_type: str = "",
    user_sub: str = "",
) -> bool:
    """Guarda ou actualiza um mapeamento no dicionário."""
    pk = _normalize_table_name(table_name)
    rk = _make_row_key(pivot_value, column_name)
    entity = {
        "PartitionKey": pk,
        "RowKey": rk,
        "PivotColumn": str(pivot_column or "").strip(),
        "PivotValue": str(pivot_value or "__global__").strip(),
        "ColumnName": str(column_name or "").strip(),
        "MappedName": str(mapped_name or "").strip(),
        "Description": str(description or "").strip()[:2000],
        "DataType": str(data_type or "").strip(),
        "UpdatedAt": datetime.now(timezone.utc).isoformat(),
        "UpdatedBy": str(user_sub or "").strip(),
    }
    # Try merge first (update), then insert
    existing = await table_query(
        _TABLE_NAME,
        f"PartitionKey eq '{pk}' and RowKey eq '{rk}'",
        top=1,
    )
    if existing:
        return await table_merge(_TABLE_NAME, entity) is not False
    return await table_insert(_TABLE_NAME, entity)


async def save_mappings_batch(
    table_name: str,
    pivot_column: str,
    mappings: list[dict],
    user_sub: str = "",
) -> int:
    """
    Guarda vários mapeamentos de uma vez.
    Cada mapping: {"pivot_value": "871", "column_name": "campo_1", "mapped_name": "session_id", "description": "...", "data_type": "uuid"}
    Retorna count de sucessos.
    """
    count = 0
    for m in (mappings or []):
        ok = await save_mapping(
            table_name=table_name,
            pivot_column=pivot_column,
            pivot_value=m.get("pivot_value", "__global__"),
            column_name=m.get("column_name", ""),
            mapped_name=m.get("mapped_name", ""),
            description=m.get("description", ""),
            data_type=m.get("data_type", ""),
            user_sub=user_sub,
        )
        if ok:
            count += 1
    return count


async def get_dictionary(table_name: str) -> list[dict]:
    """Retorna todos os mapeamentos para uma tabela."""
    pk = _normalize_table_name(table_name)
    rows = await table_query(
        _TABLE_NAME,
        f"PartitionKey eq '{pk}'",
        top=500,
    )
    return [
        {
            "pivot_value": str(r.get("PivotValue", "") or ""),
            "column_name": str(r.get("ColumnName", "") or ""),
            "mapped_name": str(r.get("MappedName", "") or ""),
            "description": str(r.get("Description", "") or ""),
            "data_type": str(r.get("DataType", "") or ""),
            "pivot_column": str(r.get("PivotColumn", "") or ""),
        }
        for r in rows
    ]


def format_dictionary_for_prompt(entries: list[dict], table_name: str = "") -> str:
    """Formata o dicionário para injecção no system prompt."""
    if not entries:
        return ""
    global_entries = [e for e in entries if e.get("pivot_value") in ("", "__global__")]
    pivot_entries = [e for e in entries if e.get("pivot_value") not in ("", "__global__")]

    lines = [f"## Dicionário de dados para {table_name or 'tabela'}"]
    if global_entries:
        lines.append("\n### Mapeamentos globais (todos os tipos de transacção):")
        for e in global_entries:
            mapped = e.get("mapped_name", "")
            desc = e.get("description", "")
            col = e.get("column_name", "")
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- {col} = **{mapped}**{suffix}")

    # Group by pivot_value
    from collections import defaultdict
    by_pivot = defaultdict(list)
    for e in pivot_entries:
        by_pivot[e["pivot_value"]].append(e)
    for pv in sorted(by_pivot.keys()):
        entries_for_pv = by_pivot[pv]
        pivot_col = entries_for_pv[0].get("pivot_column", "pivot") if entries_for_pv else "pivot"
        lines.append(f"\n### {pivot_col}={pv}:")
        for e in entries_for_pv:
            mapped = e.get("mapped_name", "")
            desc = e.get("description", "")
            col = e.get("column_name", "")
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- {col} = **{mapped}**{suffix}")

    return "\n".join(lines)
```

### Ficheiros a criar
- `data_dictionary.py`

### Testes (em `tests/test_data_dictionary.py`)
```python
class TestDataDictionary:
    async def test_save_and_retrieve_mapping(self, monkeypatch):
        ...
    async def test_save_mappings_batch(self, monkeypatch):
        ...
    def test_format_dictionary_for_prompt(self):
        ...
    def test_normalize_table_name_strips_extension(self):
        ...
```

---

## Task 5 — Registar tools `update_data_dictionary` e `get_data_dictionary`

### O que fazer

Em `tools.py`, registar duas novas tools que o LLM pode chamar:

**Tool 1: `update_data_dictionary`**
- O LLM chama esta tool quando o utilizador dá contexto sobre o significado dos campos
- Parâmetros:
  - `table_name` (string, required): nome do ficheiro/tabela fonte
  - `pivot_column` (string, optional): nome da coluna pivot (ex: `transaction_Id`)
  - `mappings` (array of objects, required): lista de mapeamentos a guardar
    - Cada item: `{"pivot_value": "871", "column_name": "campo_1", "mapped_name": "session_id", "description": "UUID da sessão", "data_type": "uuid"}`
  - `conv_id` (string, injected): conversation id
  - `user_sub` (string, injected): user sub

```python
async def tool_update_data_dictionary(
    table_name: str = "",
    pivot_column: str = "",
    mappings: list = None,
    conv_id: str = "",
    user_sub: str = "",
) -> dict:
    from data_dictionary import save_mappings_batch
    if not table_name:
        return {"error": "table_name é obrigatório."}
    if not mappings:
        return {"error": "mappings é obrigatório (lista de mapeamentos)."}
    count = await save_mappings_batch(table_name, pivot_column, mappings, user_sub)
    return {
        "status": "ok",
        "saved_count": count,
        "total_submitted": len(mappings),
        "table_name": table_name,
    }
```

**Tool 2: `get_data_dictionary`**
- O LLM chama esta tool para consultar o dicionário antes de analisar dados
- Parâmetros:
  - `table_name` (string, required): nome do ficheiro/tabela fonte

```python
async def tool_get_data_dictionary(
    table_name: str = "",
    conv_id: str = "",
) -> dict:
    from data_dictionary import get_dictionary, format_dictionary_for_prompt
    if not table_name:
        return {"error": "table_name é obrigatório."}
    entries = await get_dictionary(table_name)
    if not entries:
        return {"status": "empty", "message": f"Sem dicionário para '{table_name}'."}
    return {
        "status": "ok",
        "table_name": table_name,
        "entries_count": len(entries),
        "formatted": format_dictionary_for_prompt(entries, table_name),
        "entries": entries,
    }
```

**Definições de tool** (adicionar ao array de tool definitions em `tools.py`):

```python
{
    "type": "function",
    "function": {
        "name": "update_data_dictionary",
        "description": "Guarda mapeamentos de negócio para colunas genéricas de um dataset polimórfico. Usar quando o utilizador explicar o significado de campos como campo_1, campo_2, transaction_Id values, channel_Id values, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Nome do ficheiro ou tabela (ex: Tbl_Contact_Detail)"},
                "pivot_column": {"type": "string", "description": "Coluna que determina o significado dos campos genéricos (ex: transaction_Id)"},
                "mappings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pivot_value": {"type": "string", "description": "Valor do pivot (ex: '871') ou '__global__' para mapeamentos universais"},
                            "column_name": {"type": "string", "description": "Nome original da coluna (ex: 'campo_1' ou 'channel_Id')"},
                            "mapped_name": {"type": "string", "description": "Nome de negócio (ex: 'session_id')"},
                            "description": {"type": "string", "description": "Descrição do significado"},
                            "data_type": {"type": "string", "description": "Tipo: uuid, numeric, date, boolean, text, base64_encoded"}
                        },
                        "required": ["column_name", "mapped_name"]
                    }
                }
            },
            "required": ["table_name", "mappings"]
        }
    }
},
{
    "type": "function",
    "function": {
        "name": "get_data_dictionary",
        "description": "Consulta o dicionário de dados para um ficheiro/tabela. Retorna mapeamentos de colunas genéricas para nomes de negócio. Usar antes de analisar datasets polimórficos.",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Nome do ficheiro ou tabela (ex: Tbl_Contact_Detail)"}
            },
            "required": ["table_name"]
        }
    }
}
```

**Registar as tools** (no padrão existente):
```python
register_tool("update_data_dictionary", tool_update_data_dictionary)
register_tool("get_data_dictionary", tool_get_data_dictionary)
```

**Adicionar ao `_TOOLS_NEEDING_CONV_CONTEXT` em `agent.py`:**
```python
_TOOLS_NEEDING_CONV_CONTEXT = {
    "search_uploaded_document",
    "analyze_uploaded_table",
    "run_code",
    "classify_uploaded_emails",
    "chart_uploaded_table",
    "update_data_dictionary",  # NOVO
    "get_data_dictionary",     # NOVO
}
```

**Adicionar gate hint no system prompt** (`get_agent_system_prompt` em `tools.py`):
```python
if has_tool("update_data_dictionary"):
    gate_priority_hints.append(
        "- Se o utilizador explicar o significado de colunas genéricas (campo_N, field_N) "
        "ou valores de lookup (transaction_Id X = login, channel_Id Y = mobile), "
        "usa update_data_dictionary para guardar esses mapeamentos."
    )
    gate_priority_hints.append(
        "- Antes de analisar um dataset polimórfico, usa get_data_dictionary para "
        "consultar mapeamentos conhecidos."
    )
```

### Ficheiros a alterar
- `tools.py` (import, register, tool definitions, system prompt)
- `agent.py` (adicionar ao `_TOOLS_NEEDING_CONV_CONTEXT`)

### Testes
- Teste que verifica que as tools estão registadas
- Teste de `tool_update_data_dictionary` com monkeypatch de storage
- Teste de `tool_get_data_dictionary` com monkeypatch de storage

---

## Task 6 — Injectar dicionário automaticamente quando há ficheiro polimórfico

### O que fazer

Quando o agente tem um ficheiro polimórfico carregado E existe dicionário para a tabela
correspondente, injectar automaticamente o dicionário formatado no contexto.

Em `agent.py`, na secção onde se faz `_inject_file_context()`:

1. Para cada ficheiro com `polymorphic_schema`, tentar carregar o dicionário:
   ```python
   from data_dictionary import get_dictionary, format_dictionary_for_prompt

   for file_info in files:
       poly = file_info.get("polymorphic_schema")
       if not poly or not poly.get("is_polymorphic"):
           continue
       table_name = file_info.get("filename", "")
       try:
           entries = await get_dictionary(table_name)
           if entries:
               dict_text = format_dictionary_for_prompt(entries, table_name)
               # Anexar ao data_text ou ao contexto do ficheiro
               file_info["dictionary_context"] = dict_text
       except Exception:
           pass
   ```

2. Incluir o `dictionary_context` no bloco de sistema que descreve os ficheiros carregados.

### Ficheiros a alterar
- `agent.py`

### Testes
- Teste que verifica que `_inject_file_context` inclui dicionário quando disponível.

---

## Task 7 — Routing rule para datasets polimórficos

### O que fazer

Adicionar regra de routing no system prompt que instrui o LLM sobre como lidar com dados polimórficos:

Em `get_agent_system_prompt()` em `tools.py`, após as routing rules existentes, se
`update_data_dictionary` estiver registada:

```python
routing_rules.append(
    f"{next_rule}. Para DADOS POLIMÓRFICOS (ficheiros com campo_N cujo significado muda) -> "
    "segue este fluxo:\n"
    "   a) Primeiro usa get_data_dictionary para ver se já há mapeamentos conhecidos.\n"
    "   b) Se o utilizador explicar significados, usa update_data_dictionary para guardar.\n"
    "   c) Para analisar, SEMPRE filtrar por valor do pivot antes de interpretar campos genéricos.\n"
    "   d) Usa run_code para análise — é o mais flexível para dados polimórficos.\n"
    "   e) No código, renomeia campos genéricos usando o dicionário antes de apresentar resultados.\n"
    "   f) NUNCA misturar dados de pivot values diferentes numa mesma análise "
    "      (campo_1 significa coisas diferentes para cada tipo).\n"
    "   g) Apresenta resultados por tipo de transacção/pivot value.\n"
    "   h) Se não tiver dicionário, apresenta o perfil polimórfico e pede contexto ao utilizador."
)
next_rule += 1
```

### Ficheiros a alterar
- `tools.py`

### Testes
- Testes existentes de system prompt devem continuar a passar.

---

## Resumo de Prioridades

| Task | Prioridade | Ficheiros | Esforço |
|------|-----------|-----------|---------|
| 1 — Detecção polimórfica | **ALTA** | `tabular_loader.py` | ~200 linhas |
| 2 — Integração no upload | **ALTA** | `app.py` | ~30 linhas |
| 3 — Contexto no agent prompt | **ALTA** | `agent.py` | ~40 linhas |
| 4 — Data Dictionary storage | **ALTA** | `data_dictionary.py` (NOVO) | ~180 linhas |
| 5 — Tools update/get dictionary | **ALTA** | `tools.py`, `agent.py` | ~120 linhas |
| 6 — Injecção automática dicionário | **MÉDIA** | `agent.py` | ~30 linhas |
| 7 — Routing rule polimórfico | **MÉDIA** | `tools.py` | ~20 linhas |

## Ordem de execução recomendada
1. Task 1 (detecção — base para tudo)
2. Task 4 (data dictionary storage — independente)
3. Task 2 (integração no upload — depende de Task 1)
4. Task 5 (tools — depende de Task 4)
5. Task 3 (contexto no prompt — depende de Task 2)
6. Task 6 (injecção dicionário — depende de Task 4 + Task 3)
7. Task 7 (routing rule — depende de Task 5)

## Validação Final
```bash
python -m pytest tests/ -x -q
```
Todos os testes devem passar. Número total >= 260 (245 existentes + ~15 novos).
