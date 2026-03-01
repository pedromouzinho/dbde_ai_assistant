"""
Fixtures partilhadas para o eval suite.
IMPORTANTE: Estas fixtures NÃO chamam serviços reais por defeito.
Usar EVAL_MOCK_LLM=false para testes com serviços reais.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from . import eval_config


@pytest.fixture(scope="session")
def event_loop():
    """Event loop partilhado para testes async."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def rag_golden_set():
    """Carrega o dataset golden de RAG (50 pares Q&A)."""
    path = eval_config.DATASETS_DIR / "rag_golden_set.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def tool_scenarios():
    """Carrega cenários de teste de ferramentas."""
    path = eval_config.DATASETS_DIR / "tool_scenarios.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def arena_prompts():
    """Carrega prompts para arena comparison."""
    path = eval_config.DATASETS_DIR / "arena_prompts.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mock_llm_response():
    """Mock para respostas LLM (evita chamadas reais)."""

    def _make_response(content="Mock response", tool_calls=None):
        mock = MagicMock()
        mock.content = content
        mock.tool_calls = tool_calls or []
        mock.usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        mock.model = "mock-model"
        mock.provider = "mock"
        return mock

    return _make_response


@pytest.fixture
def mock_embedding():
    """Mock para embeddings (vector de 1536 dims)."""
    import random

    def _make_embedding(text="test"):
        random.seed(hash(text) % (2**32))
        return [random.uniform(-1, 1) for _ in range(1536)]

    return _make_embedding


@pytest.fixture
def mock_search_results():
    """Mock para resultados de Azure AI Search."""

    def _make_results(count=5):
        return {
            "total_results": count,
            "items": [
                {
                    "id": f"WI-{1000+i}",
                    "content": f"Sample work item content {i} about DevOps features and user stories.",
                    "url": f"https://dev.azure.com/ptbcp/IT.DIT/_workitems/edit/{1000+i}",
                    "tag": "User Story",
                    "status": "Active",
                    "@search.score": 0.95 - (i * 0.05),
                }
                for i in range(count)
            ],
        }

    return _make_results


@pytest.fixture
def mock_httpx_client():
    """Mock para httpx.AsyncClient."""
    client = AsyncMock()
    client.post = AsyncMock()
    client.get = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def _block_external_http(monkeypatch):
    """Block all external HTTP calls during tests."""
    if not eval_config.MOCK_LLM:
        yield
        return

    try:
        import httpx as _httpx  # noqa: F401
    except ImportError:
        yield
        return

    async def _blocked_send(self, request, **kwargs):
        raise RuntimeError(
            f"[CI Guard] Blocked external HTTP call to {request.url}. "
            "Mock this call or set EVAL_MOCK_LLM=false for real mode."
        )

    monkeypatch.setattr("httpx.AsyncClient.send", _blocked_send)
    yield
