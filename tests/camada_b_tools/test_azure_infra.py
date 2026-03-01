"""Tests para Azure infrastructure scripts (SPEC-29)."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAzureInfra:
    def test_setup_script_exists(self):
        path = os.path.join(PROJECT_ROOT, 'scripts', 'setup_azure_infra.sh')
        assert os.path.isfile(path)

    def test_setup_script_has_keyvault(self):
        path = os.path.join(PROJECT_ROOT, 'scripts', 'setup_azure_infra.sh')
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert 'keyvault create' in content
        assert 'dbde-ai-vault' in content

    def test_setup_script_has_alerts(self):
        path = os.path.join(PROJECT_ROOT, 'scripts', 'setup_azure_infra.sh')
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert 'monitor metrics alert' in content or 'alert create' in content
