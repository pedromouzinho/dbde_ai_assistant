"""Tests for PII Shield Phase 1 hardening."""

import pytest

import pii_shield
from pii_shield import (
    PIIMaskingContext,
    _CONFIDENCE_THRESHOLDS,
    _regex_pre_mask,
    _resolve_overlapping_entities,
    mask_pii,
)


class TestRegexPreFilter:
    """Tests for regex pre-masking."""

    def test_nif_detected(self):
        ctx = PIIMaskingContext()
        result = _regex_pre_mask("O NIF do cliente e 123456789", ctx)
        assert "123456789" not in result
        assert "[NIF_" in result
        assert ctx.mappings

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
        result = _regex_pre_mask("+351 912 345 678", ctx)
        assert "[TELEFONE_" in result
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
        for category in financial:
            assert _CONFIDENCE_THRESHOLDS[category] <= 0.5, f"{category} threshold too high"

    def test_general_categories_standard_threshold(self):
        assert _CONFIDENCE_THRESHOLDS["Person"] >= 0.7
        assert _CONFIDENCE_THRESHOLDS["DateTime"] >= 0.7

    def test_all_pii_categories_have_threshold(self):
        from pii_shield import PII_CATEGORIES

        for category in PII_CATEGORIES:
            assert category in _CONFIDENCE_THRESHOLDS, f"Missing threshold for {category}"


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


class TestMaskPiiIntegration:
    """Integration tests for fail-open hardening behavior."""

    @pytest.mark.asyncio
    async def test_mask_pii_uses_regex_when_azure_config_missing(self, monkeypatch):
        monkeypatch.setattr(pii_shield, "PII_ENABLED", True)
        monkeypatch.setattr(pii_shield, "PII_ENDPOINT", "")
        monkeypatch.setattr(pii_shield, "PII_API_KEY", "")

        ctx = PIIMaskingContext()
        result = await mask_pii("O NIF do cliente e 123456789", ctx)

        assert "123456789" not in result
        assert "[NIF_" in result
        assert ctx.unmask(result) == "O NIF do cliente e 123456789"

    @pytest.mark.asyncio
    async def test_mask_pii_keeps_regex_mask_when_azure_fails(self, monkeypatch):
        class _FailingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *args, **kwargs):
                raise pii_shield.httpx.TimeoutException("timeout")

        monkeypatch.setattr(pii_shield, "PII_ENABLED", True)
        monkeypatch.setattr(pii_shield, "PII_ENDPOINT", "https://pii.example.test")
        monkeypatch.setattr(pii_shield, "PII_API_KEY", "test-key")
        monkeypatch.setattr(pii_shield.httpx, "AsyncClient", _FailingClient)

        ctx = PIIMaskingContext()
        result = await mask_pii("IBAN: PT50000201231234567890154", ctx)

        assert "PT50" not in result
        assert "[IBAN_" in result
        assert ctx.unmask(result) == "IBAN: PT50000201231234567890154"

    @pytest.mark.asyncio
    async def test_mask_pii_skips_azure_entities_inside_placeholders(self, monkeypatch):
        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": {
                        "documents": [
                            {
                                "entities": [
                                    {
                                        "offset": 6,
                                        "length": 5,
                                        "category": "Quantity",
                                        "confidenceScore": 0.99,
                                    }
                                ]
                            }
                        ]
                    }
                }

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *args, **kwargs):
                return _Response()

        monkeypatch.setattr(pii_shield, "PII_ENABLED", True)
        monkeypatch.setattr(pii_shield, "PII_ENDPOINT", "https://pii.example.test")
        monkeypatch.setattr(pii_shield, "PII_API_KEY", "test-key")
        monkeypatch.setattr(pii_shield.httpx, "AsyncClient", _Client)

        ctx = PIIMaskingContext()
        result = await mask_pii("NIF: 123456789", ctx)

        assert result == "NIF: [NIF_1]"
        assert len(ctx.mappings) == 1
        assert ctx.unmask(result) == "NIF: 123456789"
