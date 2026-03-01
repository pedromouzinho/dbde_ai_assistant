"""Tests para deploy infrastructure scripts (SPEC-28)."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDeployInfra:
    def test_smoke_script_exists(self):
        path = os.path.join(PROJECT_ROOT, 'scripts', 'smoke_test.py')
        assert os.path.isfile(path)

    def test_deploy_swap_exists(self):
        path = os.path.join(PROJECT_ROOT, 'scripts', 'deploy_swap.sh')
        assert os.path.isfile(path)

    def test_rollback_exists(self):
        path = os.path.join(PROJECT_ROOT, 'scripts', 'rollback.sh')
        assert os.path.isfile(path)

    def test_deploy_script_uses_swap(self):
        path = os.path.join(PROJECT_ROOT, 'scripts', 'deploy_swap.sh')
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert 'deployment slot swap' in content
        assert 'smoke_test.py' in content
