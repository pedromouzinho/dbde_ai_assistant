"""
PII Shield — mascara dados pessoais antes de enviar ao LLM.
Usa Azure AI Language (Text Analytics) PII Detection.
"""

from __future__ import annotations

import logging
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


async def mask_pii(text: str, context: PIIMaskingContext) -> str:
    """
    Envia texto ao Azure AI Language PII Detection.
    Devolve texto com PII mascarado e popula o context com os mappings.
    """
    if not PII_ENABLED or not PII_ENDPOINT or not PII_API_KEY:
        return text

    if not text or len(text.strip()) < 3:
        return text

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

        # Substituir do fim para o início para manter offsets válidos.
        entities.sort(key=lambda e: e.get("offset", 0), reverse=True)

        masked = text
        masked_count = 0
        for entity in entities:
            if float(entity.get("confidenceScore", 0)) < 0.7:
                continue
            offset = int(entity.get("offset", 0))
            length = int(entity.get("length", 0))
            if length <= 0:
                continue
            category = str(entity.get("category", "UNKNOWN"))
            original = masked[offset : offset + length]
            placeholder = context.add_mapping(category, original)
            masked = masked[:offset] + placeholder + masked[offset + length :]
            masked_count += 1

        logger.info("PII Shield: mascaradas %d entidades", masked_count)
        return masked
    except Exception as e:
        logger.warning("PII Shield falhou (passthrough sem mascara): %s", e)
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

