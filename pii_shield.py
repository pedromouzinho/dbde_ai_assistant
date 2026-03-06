"""
PII Shield — mascara dados pessoais antes de enviar ao LLM.
Usa Azure AI Language (Text Analytics) PII Detection.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Any

import httpx

from config import PII_ENDPOINT, PII_API_KEY, PII_ENABLED

logger = logging.getLogger(__name__)

# Categorias PII a mascarar (portuguesas e internacionais)
PII_CATEGORIES = [
    "Person",
    "PersonType",
    "PhoneNumber",
    "Address",
    "Email",
    "URL",
    "IPAddress",
    "DateTime",
    "Quantity",
    "PTTaxIdentificationNumber",
    "InternationalBankingAccountNumber",
    "SWIFTCode",
    "CreditCardNumber",
    "EUDriversLicenseNumber",
    "EUPassportNumber",
    "EUSocialSecurityNumber",
    "EUTaxIdentificationNumber",
]

# Thresholds per category — lower = more aggressive masking
# Financial and identity categories use lower thresholds to prefer
# false positives over data leaks.
_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "PTTaxIdentificationNumber": 0.4,
    "InternationalBankingAccountNumber": 0.4,
    "CreditCardNumber": 0.4,
    "SWIFTCode": 0.4,
    "EUSocialSecurityNumber": 0.4,
    "EUPassportNumber": 0.5,
    "EUDriversLicenseNumber": 0.5,
    "EUTaxIdentificationNumber": 0.4,
    "PhoneNumber": 0.6,
    "Email": 0.6,
    "Person": 0.7,
    "PersonType": 0.7,
    "Address": 0.7,
    "URL": 0.8,
    "IPAddress": 0.7,
    "DateTime": 0.8,
    "Quantity": 0.8,
}

_DEFAULT_THRESHOLD = 0.7

_REGEX_PATTERNS: list[tuple[str, str]] = [
    (r"\b[1-35-689]\d{8}\b", "PTTaxIdentificationNumber"),
    (r"\bPT\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{3}\b", "InternationalBankingAccountNumber"),
    (r"\b[A-Z]{2}\d{2}\s?[\dA-Z]{4}(?:\s?[\dA-Z]{4}){2,7}(?:\s?[\dA-Z]{1,4})?\b", "InternationalBankingAccountNumber"),
    (r"\b(?:\d[ -]?){13,19}\b", "CreditCardNumber"),
    (r"\b[A-Z]{4}[A-Z]{2}[A-Z\d]{2}(?:[A-Z\d]{3})?\b", "SWIFTCode"),
    (r"(?:\+351|00351)[\s.-]?\d{3}[\s.-]?\d{3}[\s.-]?\d{3}\b", "PhoneNumber"),
    (r"\b[923]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b", "PhoneNumber"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "Email"),
    (r"\b\d{11}\b", "EUSocialSecurityNumber"),
]

_COMPILED_REGEX = [
    (re.compile(pattern, re.IGNORECASE if category != "SWIFTCode" else 0), category)
    for pattern, category in _REGEX_PATTERNS
]

_PLACEHOLDER_PATTERN = re.compile(r"\[[^\[\]]+_\d+\]")

_PRIORITY_CATEGORIES = {
    "InternationalBankingAccountNumber",
    "CreditCardNumber",
    "PTTaxIdentificationNumber",
    "SWIFTCode",
    "EUSocialSecurityNumber",
    "EUPassportNumber",
    "EUDriversLicenseNumber",
    "EUTaxIdentificationNumber",
}


class PIIMaskingContext:
    """Guarda o mapeamento mask -> valor real para desmascarar depois."""

    def __init__(self):
        self.mappings: Dict[str, str] = {}
        self._counters: Dict[str, int] = {}

    def add_mapping(self, category: str, original: str) -> str:
        """Regista um valor PII e devolve o placeholder."""
        cat_label = _category_to_label(category)
        count = self._counters.get(cat_label, 0) + 1
        self._counters[cat_label] = count
        placeholder = f"[{cat_label}_{count}]"
        self.mappings[placeholder] = original
        return placeholder

    def unmask(self, text: str) -> str:
        """Substitui placeholders pelos valores reais."""
        unmasked = text
        for placeholder, original in self.mappings.items():
            unmasked = unmasked.replace(placeholder, original)
        return unmasked

    def unmask_any(self, value: Any) -> Any:
        """Desmascara strings dentro de estruturas aninhadas (dict/list/str)."""
        if isinstance(value, str):
            return self.unmask(value)
        if isinstance(value, list):
            return [self.unmask_any(v) for v in value]
        if isinstance(value, dict):
            return {k: self.unmask_any(v) for k, v in value.items()}
        return value


def _category_to_label(category: str) -> str:
    """Converte categoria Azure PII para label legível."""
    labels = {
        "Person": "NOME",
        "PersonType": "TIPO_PESSOA",
        "PhoneNumber": "TELEFONE",
        "Address": "MORADA",
        "Email": "EMAIL",
        "PTTaxIdentificationNumber": "NIF",
        "InternationalBankingAccountNumber": "IBAN",
        "CreditCardNumber": "CARTAO",
        "SWIFTCode": "SWIFT",
        "EUPassportNumber": "PASSAPORTE",
        "EUSocialSecurityNumber": "NISS",
        "EUDriversLicenseNumber": "CARTA_CONDUCAO",
    }
    return labels.get(category, category.upper())


def _regex_pre_mask(text: str, context: PIIMaskingContext) -> str:
    """
    Local regex pre-filter: catches high-value PII patterns before Azure API.
    Runs synchronously, no network call. Acts as safety net for API failures.
    """
    if not text or len(text.strip()) < 3:
        return text

    all_matches: list[tuple[int, int, str, str]] = []
    for pattern, category in _COMPILED_REGEX:
        for match in pattern.finditer(text):
            all_matches.append((match.start(), match.end(), category, match.group()))

    if not all_matches:
        return text

    all_matches.sort(key=lambda match: (match[0], -(match[1] - match[0])))

    resolved: list[tuple[int, int, str, str]] = []
    last_end = -1
    for start, end, category, original in all_matches:
        if start >= last_end:
            resolved.append((start, end, category, original))
            last_end = end

    masked = text
    for start, end, category, original in reversed(resolved):
        placeholder = context.add_mapping(category, original)
        masked = masked[:start] + placeholder + masked[end:]

    return masked


def _resolve_overlapping_entities(entities: list[dict]) -> list[dict]:
    """
    Resolve overlapping entities by preferring priority categories first,
    then higher confidence, then longer matches.
    """
    if len(entities) <= 1:
        return entities

    sorted_ents = sorted(
        entities,
        key=lambda entity: (int(entity.get("offset", 0)), -int(entity.get("length", 0))),
    )

    resolved: list[dict] = []
    for entity in sorted_ents:
        offset = int(entity.get("offset", 0))
        length = int(entity.get("length", 0))

        if resolved:
            previous = resolved[-1]
            previous_end = int(previous.get("offset", 0)) + int(previous.get("length", 0))
            if offset < previous_end:
                previous_score = float(previous.get("confidenceScore", 0))
                current_score = float(entity.get("confidenceScore", 0))
                previous_cat = str(previous.get("category", ""))
                current_cat = str(entity.get("category", ""))
                previous_priority = previous_cat in _PRIORITY_CATEGORIES
                current_priority = current_cat in _PRIORITY_CATEGORIES

                replace = False
                if current_priority and not previous_priority:
                    replace = True
                elif previous_priority and not current_priority:
                    replace = False
                elif current_score > previous_score:
                    replace = True
                elif current_score == previous_score and length > int(previous.get("length", 0)):
                    replace = True

                if replace:
                    resolved[-1] = entity
                continue

        resolved.append(entity)

    return resolved


def _span_overlaps_placeholders(offset: int, length: int, placeholder_spans: list[tuple[int, int]]) -> bool:
    """Ignore Azure detections that hit placeholders created by local masking."""
    end = offset + length
    for placeholder_start, placeholder_end in placeholder_spans:
        if offset < placeholder_end and end > placeholder_start:
            return True
    return False


async def mask_pii(text: str, context: PIIMaskingContext) -> str:
    """
    Envia texto ao Azure AI Language PII Detection.
    Devolve texto com PII mascarado e popula o context com os mappings.
    """
    if not PII_ENABLED or not PII_ENDPOINT or not PII_API_KEY:
        if PII_ENABLED:
            return _regex_pre_mask(text, context)
        return text

    if not text or len(text.strip()) < 3:
        return text

    text = _regex_pre_mask(text, context)
    placeholder_spans = [match.span() for match in _PLACEHOLDER_PATTERN.finditer(text)]

    try:
        url = f"{PII_ENDPOINT}/language/:analyze-text?api-version=2023-04-01"

        payload = {
            "kind": "PiiEntityRecognition",
            "parameters": {
                "modelVersion": "latest",
                "piiCategories": PII_CATEGORIES,
                "domain": "none",
                "stringIndexType": "Utf16CodeUnit",
            },
            "analysisInput": {
                "documents": [{"id": "1", "language": "pt", "text": text}],
            },
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Ocp-Apim-Subscription-Key": PII_API_KEY,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()

        result = resp.json()
        doc = (result.get("results", {}).get("documents") or [{}])[0]
        entities = doc.get("entities", [])
        if not entities:
            return text

        filtered_entities: list[dict] = []
        for entity in entities:
            category = str(entity.get("category", "UNKNOWN"))
            threshold = _CONFIDENCE_THRESHOLDS.get(category, _DEFAULT_THRESHOLD)
            if float(entity.get("confidenceScore", 0)) < threshold:
                continue

            offset = int(entity.get("offset", 0))
            length = int(entity.get("length", 0))
            if length <= 0:
                continue
            if _span_overlaps_placeholders(offset, length, placeholder_spans):
                continue
            filtered_entities.append(entity)

        if not filtered_entities:
            return text

        entities = _resolve_overlapping_entities(filtered_entities)
        entities.sort(key=lambda entity: entity.get("offset", 0), reverse=True)

        masked = text
        masked_count = 0
        for entity in entities:
            offset = int(entity.get("offset", 0))
            length = int(entity.get("length", 0))
            category = str(entity.get("category", "UNKNOWN"))
            original = masked[offset : offset + length]
            placeholder = context.add_mapping(category, original)
            masked = masked[:offset] + placeholder + masked[offset + length :]
            masked_count += 1

        logger.info("PII Shield: mascaradas %d entidades", masked_count)
        return masked
    except Exception as e:
        logger.warning("PII Shield falhou na API Azure (mantida mascara local, se aplicada): %s", e)
        return text


async def mask_messages(messages: List[dict], context: PIIMaskingContext) -> List[dict]:
    """Mascara PII apenas em mensagens do utilizador."""
    masked_messages: List[dict] = []
    for msg in messages:
        if msg.get("role") != "user":
            masked_messages.append(msg)
            continue

        content = msg.get("content", "")
        if isinstance(content, str):
            masked_content = await mask_pii(content, context)
            masked_messages.append({**msg, "content": masked_content})
            continue

        if isinstance(content, list):
            masked_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    masked_text = await mask_pii(str(part.get("text", "")), context)
                    masked_parts.append({**part, "text": masked_text})
                else:
                    masked_parts.append(part)
            masked_messages.append({**msg, "content": masked_parts})
            continue

        masked_messages.append(msg)

    return masked_messages
