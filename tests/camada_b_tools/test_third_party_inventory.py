"""Tests para inventario de terceiros (SPEC-26)."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestThirdPartyInventory:
    def test_inventory_exists(self):
        path = os.path.join(PROJECT_ROOT, "docs", "THIRD_PARTY_INVENTORY.md")
        assert os.path.isfile(path)

    def test_inventory_has_core_services(self):
        path = os.path.join(PROJECT_ROOT, "docs", "THIRD_PARTY_INVENTORY.md")
        with open(path, encoding="utf-8") as f:
            content = f.read().lower()
        required = [
            "azure openai",
            "azure ai search",
            "azure devops",
            "brave",
            "figma",
            "miro",
            "pii",
            "jurisdição",
        ]
        for item in required:
            assert item in content, f"Missing required inventory item: {item}"
