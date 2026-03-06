# Codex Task: PII Shield Phase 1 — Hardening

## Context

File to modify: `pii_shield.py` (190 lines)
Tests dir: `tests/`
Branch: work on the feature branch, commit when done.

The PII Shield uses Azure AI Language PII Detection API to mask sensitive data
before sending to the LLM. This task hardens it with three improvements:
1. Regex pre-filter for high-value entities (catches what Azure misses)
2. Differentiated confidence thresholds per category
3. Proper overlapping entity resolution

## Current Architecture

```
User text → mask_pii() → Azure PII API → entities list → replace from end → masked text
```

Key files:
- `pii_shield.py`: All PII logic (masking, unmasking, categories)
- `config.py`: PII_ENABLED, PII_ENDPOINT, PII_API_KEY flags
- `llm_provider.py:822-825`: Calls mask_messages() before LLM call
- `llm_provider.py:856-869`: Calls unmask after LLM response

## Task 1: Regex Pre-Filter (LOCAL, before Azure API call)

### Goal
Add a regex-based pre-masking step that runs BEFORE the Azure API call.
This catches obvious patterns that Azure might miss (confidence < 0.7) or
that fail due to API timeout (fail-open behavior).

### Implementation

Add a new function `_regex_pre_mask()` in `pii_shield.py` that runs before
the Azure API call. It should detect and mask:

```python
import re

# Add these patterns near the top of pii_shield.py, after the imports

_REGEX_PATTERNS: list[tuple[str, str]] = [
    # Portuguese NIF: 9 digits starting with 1-3,5,6,8,9
    (r"\b[1-35-689]\d{8}\b", "PTTaxIdentificationNumber"),
    # IBAN: PT50 followed by 23 digits (with optional spaces)
    (r"\bPT\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{3}\b", "InternationalBankingAccountNumber"),
    # Generic IBAN (2 letter country + 2 check digits + up to 30 alphanumeric)
    (r"\b[A-Z]{2}\d{2}\s?[\dA-Z]{4}(?:\s?[\dA-Z]{4}){2,7}(?:\s?[\dA-Z]{1,4})?\b", "InternationalBankingAccountNumber"),
    # Credit card: 13-19 digits with optional spaces/dashes (Luhn not checked here)
    (r"\b(?:\d[ -]?){13,19}\b", "CreditCardNumber"),
    # SWIFT/BIC: 8 or 11 alphanumeric (4 bank + 2 country + 2 location + optional 3 branch)
    (r"\b[A-Z]{4}[A-Z]{2}[A-Z\d]{2}(?:[A-Z\d]{3})?\b", "SWIFTCode"),
    # Portuguese phone: +351 or 00351 followed by 9 digits
    (r"(?:\+351|00351)[\s.-]?\d{3}[\s.-]?\d{3}[\s.-]?\d{3}\b", "PhoneNumber"),
    # Portuguese phone: 9 digits starting with 9, 2, or 3
    (r"\b[923]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b", "PhoneNumber"),
    # Email (standard pattern)
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "Email"),
    # NISS (Portuguese Social Security): 11 digits
    (r"\b\d{11}\b", "EUSocialSecurityNumber"),
]

# Compile once at module level
_COMPILED_REGEX = [(re.compile(p, re.IGNORECASE if cat != "SWIFTCode" else 0), cat) for p, cat in _REGEX_PATTERNS]
```

Then add the pre-mask function:

```python
def _regex_pre_mask(text: str, context: PIIMaskingContext) -> str:
    """
    Local regex pre-filter: catches high-value PII patterns before Azure API.
    Runs synchronously, no network call. Acts as safety net for API failures.
    """
    if not text or len(text.strip()) < 3:
        return text

    # Collect all matches with their spans
    all_matches: list[tuple[int, int, str, str]] = []  # (start, end, category, original)
    for pattern, category in _COMPILED_REGEX:
        for match in pattern.finditer(text):
            all_matches.append((match.start(), match.end(), category, match.group()))

    if not all_matches:
        return text

    # Sort by start position, then by length (longer match wins)
    all_matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))

    # Resolve overlaps: greedy, longer match wins
    resolved: list[tuple[int, int, str, str]] = []
    last_end = -1
    for start, end, category, original in all_matches:
        if start >= last_end:
            resolved.append((start, end, category, original))
            last_end = end

    # Replace from end to preserve offsets
    masked = text
    for start, end, category, original in reversed(resolved):
        placeholder = context.add_mapping(category, original)
        masked = masked[:start] + placeholder + masked[end:]

    return masked
```

### Integration point

In `mask_pii()`, call `_regex_pre_mask()` BEFORE the Azure API call:

```python
async def mask_pii(text: str, context: PIIMaskingContext) -> str:
    if not PII_ENABLED or not PII_ENDPOINT or not PII_API_KEY:
        # Even without Azure API, apply regex pre-filter
        if PII_ENABLED:
            return _regex_pre_mask(text, context)
        return text

    if not text or len(text.strip()) < 3:
        return text

    # Step 1: Regex pre-filter (local, fast, no network)
    text = _regex_pre_mask(text, context)

    # Step 2: Azure API call (existing logic, but skip already-masked placeholders)
    # ... rest of existing Azure API logic ...
```

IMPORTANT: The regex pre-filter must run even when Azure API is unavailable
(fail-open scenario). This is the safety net.

## Task 2: Differentiated Confidence Thresholds

### Goal
Replace the single 0.7 threshold with per-category thresholds.
Financial/identity entities need LOWER thresholds (more aggressive masking).

### Implementation

Add this dict near the top of `pii_shield.py`:

```python
# Thresholds per category — lower = more aggressive masking
# Financial and identity categories use 0.4 (prefer false positive over leak)
# General categories keep 0.7 (avoid over-masking names, dates, etc.)
_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "PTTaxIdentificationNumber": 0.4,        # NIF — critical
    "InternationalBankingAccountNumber": 0.4, # IBAN — critical
    "CreditCardNumber": 0.4,                  # Card number — critical
    "SWIFTCode": 0.4,                         # SWIFT — critical
    "EUSocialSecurityNumber": 0.4,            # NISS — critical
    "EUPassportNumber": 0.5,                  # Passport — high
    "EUDriversLicenseNumber": 0.5,            # Driver license — high
    "EUTaxIdentificationNumber": 0.4,         # EU Tax ID — critical
    "PhoneNumber": 0.6,                       # Phone — medium-high
    "Email": 0.6,                             # Email — medium-high
    "Person": 0.7,                            # Names — standard
    "PersonType": 0.7,                        # Person type — standard
    "Address": 0.7,                           # Address — standard
    "URL": 0.8,                               # URLs — higher (lots of false positives)
    "IPAddress": 0.7,                         # IP — standard
    "DateTime": 0.8,                          # DateTime — higher (avoid over-masking)
    "Quantity": 0.8,                          # Quantity — higher (avoid over-masking)
}

_DEFAULT_THRESHOLD = 0.7
```

Then replace the hardcoded threshold in `mask_pii()`:

```python
# OLD (line 142):
# if float(entity.get("confidenceScore", 0)) < 0.7:

# NEW:
category = str(entity.get("category", "UNKNOWN"))
threshold = _CONFIDENCE_THRESHOLDS.get(category, _DEFAULT_THRESHOLD)
if float(entity.get("confidenceScore", 0)) < threshold:
    continue
```

Note: move the `category` extraction BEFORE the threshold check (currently
it's after on line 148). The offset/length extraction stays where it is.

## Task 3: Overlapping Entity Resolution

### Goal
Currently entities are sorted by offset in reverse and replaced sequentially.
If two entities overlap (e.g., an IBAN detected as both IBAN and a sequence
of numbers), both get masked, potentially corrupting the text.

### Implementation

Add an overlap resolution function:

```python
def _resolve_overlapping_entities(entities: list[dict]) -> list[dict]:
    """
    Resolve overlapping entities: when two entities overlap, keep the one
    with higher confidence. If equal confidence, prefer the longer match.
    If equal length, prefer financial/identity categories.
    """
    if len(entities) <= 1:
        return entities

    # Sort by offset ascending, then by length descending
    sorted_ents = sorted(
        entities,
        key=lambda e: (int(e.get("offset", 0)), -int(e.get("length", 0)))
    )

    # Priority categories (prefer masking these over generic ones)
    _PRIORITY_CATEGORIES = {
        "InternationalBankingAccountNumber", "CreditCardNumber",
        "PTTaxIdentificationNumber", "SWIFTCode",
        "EUSocialSecurityNumber", "EUPassportNumber",
        "EUDriversLicenseNumber", "EUTaxIdentificationNumber",
    }

    resolved: list[dict] = []
    for entity in sorted_ents:
        offset = int(entity.get("offset", 0))
        length = int(entity.get("length", 0))
        end = offset + length

        # Check overlap with last resolved entity
        if resolved:
            prev = resolved[-1]
            prev_end = int(prev.get("offset", 0)) + int(prev.get("length", 0))
            if offset < prev_end:
                # Overlap detected — pick winner
                prev_score = float(prev.get("confidenceScore", 0))
                curr_score = float(entity.get("confidenceScore", 0))
                prev_cat = str(prev.get("category", ""))
                curr_cat = str(entity.get("category", ""))
                prev_priority = prev_cat in _PRIORITY_CATEGORIES
                curr_priority = curr_cat in _PRIORITY_CATEGORIES

                # Decision: priority category wins, then higher confidence, then longer
                replace = False
                if curr_priority and not prev_priority:
                    replace = True
                elif prev_priority and not curr_priority:
                    replace = False
                elif curr_score > prev_score:
                    replace = True
                elif curr_score == prev_score and length > int(prev.get("length", 0)):
                    replace = True

                if replace:
                    resolved[-1] = entity
                continue

        resolved.append(entity)

    return resolved
```

Integrate into `mask_pii()`, after getting entities from Azure:

```python
# After line 133 (if not entities: return text)
# Add:
entities = _resolve_overlapping_entities(entities)

# Then continue with existing reverse-sort and replacement logic
entities.sort(key=lambda e: e.get("offset", 0), reverse=True)
```

## Testing Requirements

Create `tests/test_pii_shield_hardening.py`:

```python
"""Tests for PII Shield Phase 1 hardening."""
import pytest
from pii_shield import (
    PIIMaskingContext,
    _regex_pre_mask,
    _resolve_overlapping_entities,
    _CONFIDENCE_THRESHOLDS,
)


class TestRegexPreFilter:
    """Tests for regex pre-masking."""

    def test_nif_detected(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("O NIF do cliente e 123456789", ctx)
        assert "123456789" not in result
        assert "[NIF_" in result
        assert ctx.mappings  # should have a mapping

    def test_iban_pt_detected(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("IBAN: PT50000201231234567890154", ctx)
        assert "PT50" not in result
        assert "[IBAN_" in result

    def test_iban_with_spaces(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("PT50 0002 0123 1234 5678 9015 4", ctx)
        assert "[IBAN_" in result

    def test_credit_card_detected(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("Cartao: 4111 1111 1111 1111", ctx)
        assert "4111" not in result
        assert "[CARTAO_" in result

    def test_email_detected(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("Email: joao.silva@millennium.pt", ctx)
        assert "joao.silva" not in result
        assert "[EMAIL_" in result

    def test_phone_pt_detected(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("Telefone: +351 912 345 678", ctx)
        assert "912" not in result
        assert "[TELEFONE_" in result

    def test_swift_detected(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("SWIFT: BCOMPTPL", ctx)
        assert "BCOMPTPL" not in result
        assert "[SWIFT_" in result

    def test_no_false_positive_short_numbers(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("Tenho 42 items", ctx)
        assert result == "Tenho 42 items"

    def test_unmask_roundtrip(self):
        ctx = PIIMaskingContext()
        original = "NIF: 123456789, IBAN: PT50000201231234567890154"
        masked = _regex_pre_mask(original, ctx)
        unmasked = ctx.unmask(masked)
        assert unmasked == original

    def test_overlapping_regex_matches(self):
        """When regex patterns overlap, longer match should win."""
        ctx = PIIMaskingContext()
        # A phone number that could also match NIF pattern
        result = _regex_pre_mask("+351 912 345 678", ctx)
        # Should be masked as phone, not partially as NIF
        assert len(ctx.mappings) == 1

    def test_empty_text(self):
        ctx = PIIMaskingContext()
        assert _regex_pre_mask("", ctx) == ""
        assert _regex_pre_mask("ab", ctx) == "ab"


class TestDifferentiatedThresholds:
    """Tests for per-category confidence thresholds."""

    def test_financial_categories_lower_threshold(self):
        financial = [
            "PTTaxIdentificationNumber",
            "InternationalBankingAccountNumber",
            "CreditCardNumber",
            "SWIFTCode",
            "EUSocialSecurityNumber",
        ]
        for cat in financial:
            assert _CONFIDENCE_THRESHOLDS[cat] <= 0.5, f"{cat} threshold too high"

    def test_general_categories_standard_threshold(self):
        assert _CONFIDENCE_THRESHOLDS["Person"] >= 0.7
        assert _CONFIDENCE_THRESHOLDS["DateTime"] >= 0.7

    def test_all_pii_categories_have_threshold(self):
        from pii_shield import PII_CATEGORIES
        for cat in PII_CATEGORIES:
            assert cat in _CONFIDENCE_THRESHOLDS, f"Missing threshold for {cat}"


class TestOverlappingEntityResolution:
    """Tests for overlapping entity resolution."""

    def test_no_overlap(self):
        entities = [
            {"offset": 0, "length": 5, "category": "Person", "confidenceScore": 0.9},
            {"offset": 10, "length": 9, "category": "PTTaxIdentificationNumber", "confidenceScore": 0.8},
        ]
        result = _resolve_overlapping_entities(entities)
        assert len(result) == 2

    def test_overlap_higher_confidence_wins(self):
        entities = [
            {"offset": 0, "length": 10, "category": "Quantity", "confidenceScore": 0.7},
            {"offset": 5, "length": 10, "category": "Person", "confidenceScore": 0.9},
        ]
        result = _resolve_overlapping_entities(entities)
        assert len(result) == 1
        assert result[0]["category"] == "Person"

    def test_overlap_priority_category_wins(self):
        entities = [
            {"offset": 0, "length": 25, "category": "Quantity", "confidenceScore": 0.95},
            {"offset": 0, "length": 25, "category": "InternationalBankingAccountNumber", "confidenceScore": 0.8},
        ]
        result = _resolve_overlapping_entities(entities)
        assert len(result) == 1
        assert result[0]["category"] == "InternationalBankingAccountNumber"

    def test_empty_list(self):
        assert _resolve_overlapping_entities([]) == []

    def test_single_entity(self):
        entities = [{"offset": 0, "length": 5, "category": "Person", "confidenceScore": 0.9}]
        result = _resolve_overlapping_entities(entities)
        assert len(result) == 1
```

## Files to Modify

1. **`pii_shield.py`** — Main changes (all three tasks)
2. **`tests/test_pii_shield_hardening.py`** — New test file

## Files NOT to Modify

- `config.py` — No changes needed
- `llm_provider.py` — No changes needed (mask_messages interface unchanged)
- `agent.py` — No changes needed

## Acceptance Criteria

1. `_regex_pre_mask()` catches NIF, IBAN, credit card, SWIFT, phone, email without Azure API
2. Regex pre-filter runs even when Azure API is unavailable (fail-open safety net)
3. Financial entities masked at 0.4 threshold, general at 0.7
4. Overlapping entities resolved correctly (priority category > confidence > length)
5. `unmask()` roundtrip works correctly for regex-masked entities
6. All existing tests still pass
7. New tests in `test_pii_shield_hardening.py` all pass
8. No breaking changes to `mask_messages()` or `PIIMaskingContext` public interface

## Execution Order

1. Add regex patterns and `_regex_pre_mask()` function
2. Add `_CONFIDENCE_THRESHOLDS` dict
3. Add `_resolve_overlapping_entities()` function
4. Modify `mask_pii()` to integrate all three
5. Create test file
6. Run tests: `python -m pytest tests/test_pii_shield_hardening.py -v`
7. Run existing tests to verify no regressions
