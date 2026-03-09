import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import agent


@pytest.fixture(autouse=True)
def _reset_agent_state():
    agent.conversation_meta.clear()
    agent.uploaded_files_store.clear()
    agent._conversation_locks.clear()
    yield
    agent.conversation_meta.clear()
    agent.uploaded_files_store.clear()
    agent._conversation_locks.clear()


class TestConversationStoreLocking:
    """Verify that ConversationStore remains safe under concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_set_no_data_loss(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)

        async def writer(key: str) -> None:
            await store.async_set(key, [{"role": "user", "content": f"msg-{key}"}])

        await asyncio.gather(*(writer(f"conv-{idx}") for idx in range(10)))

        assert len(store) == 10

    @pytest.mark.asyncio
    async def test_concurrent_set_same_key_last_wins(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        first_done = asyncio.Event()

        async def writer_a() -> None:
            await store.async_set("k", [{"v": "a"}])
            first_done.set()

        async def writer_b() -> None:
            await first_done.wait()
            await store.async_set("k", [{"v": "b"}])

        await asyncio.gather(writer_a(), writer_b())

        assert await store.async_get("k") == [{"v": "b"}]

    @pytest.mark.asyncio
    async def test_capacity_eviction_under_concurrency(self):
        store = agent.ConversationStore(max_conversations=5, ttl_seconds=3600)

        async def writer(key: str) -> None:
            await store.async_set(key, [{"role": "user", "content": key}])

        await asyncio.gather(*(writer(f"c-{idx}") for idx in range(20)))

        assert len(store) <= 5

    @pytest.mark.asyncio
    async def test_async_delete_removes_entry(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)

        await store.async_set("x", [{"m": 1}])
        await store.async_delete("x")

        assert not await store.async_contains("x")

    @pytest.mark.asyncio
    async def test_async_cleanup_expired_removes_stale_entries(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=1)
        await store.async_set("expired", [{"m": 1}])
        store._last_accessed["expired"] = datetime.now(timezone.utc) - timedelta(seconds=10)

        expired = await store.async_cleanup_expired()

        assert expired == ["expired"]
        assert not await store.async_contains("expired")


class TestHTTPClientLocking:
    """Verify that provider clients are initialized once under concurrency."""

    @pytest.mark.asyncio
    async def test_azure_provider_single_client(self, monkeypatch):
        from llm_provider import AzureOpenAIProvider

        monkeypatch.setattr("llm_provider.AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
        monkeypatch.setattr("llm_provider.AZURE_OPENAI_KEY", "test-key")

        provider = AzureOpenAIProvider(deployment="test")
        clients = await asyncio.gather(*(provider._get_client() for _ in range(10)))

        assert len({id(client) for client in clients}) == 1
        await provider.close()

    @pytest.mark.asyncio
    async def test_azure_provider_close_recreates_client(self, monkeypatch):
        from llm_provider import AzureOpenAIProvider

        monkeypatch.setattr("llm_provider.AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
        monkeypatch.setattr("llm_provider.AZURE_OPENAI_KEY", "test-key")

        provider = AzureOpenAIProvider(deployment="test")
        first = await provider._get_client()
        await provider.close()
        second = await provider._get_client()

        assert id(first) != id(second)
        await provider.close()

    @pytest.mark.asyncio
    async def test_anthropic_provider_single_client(self, monkeypatch):
        from llm_provider import AnthropicProvider

        monkeypatch.setattr("llm_provider.ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("llm_provider.ANTHROPIC_API_BASE", "https://test.anthropic.com")

        provider = AnthropicProvider(model="test")
        clients = await asyncio.gather(*(provider._get_client() for _ in range(10)))

        assert len({id(client) for client in clients}) == 1
        await provider.close()

    @pytest.mark.asyncio
    async def test_anthropic_provider_close_recreates_client(self, monkeypatch):
        from llm_provider import AnthropicProvider

        monkeypatch.setattr("llm_provider.ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr("llm_provider.ANTHROPIC_API_BASE", "https://test.anthropic.com")

        provider = AnthropicProvider(model="test")
        first = await provider._get_client()
        await provider.close()
        second = await provider._get_client()

        assert id(first) != id(second)
        await provider.close()


class TestConversationLockSafety:
    """Verify that per-conversation locks are created and cleaned up safely."""

    @pytest.mark.asyncio
    async def test_concurrent_lock_acquisition_same_conv(self):
        locks = await asyncio.gather(
            agent._get_conversation_lock("conv-1"),
            agent._get_conversation_lock("conv-1"),
        )

        assert locks[0] is locks[1]

    @pytest.mark.asyncio
    async def test_lock_not_evicted_while_held(self):
        lock = await agent._get_conversation_lock("conv-held")
        agent.conversation_meta["conv-held"] = {"mode": "general"}
        agent.uploaded_files_store["conv-held"] = {"files": []}

        async with lock:
            agent._cleanup_conversation_related_state("conv-held")

            assert "conv-held" not in agent.conversation_meta
            assert "conv-held" not in agent.uploaded_files_store
            assert agent._conversation_locks.get("conv-held") is lock

    @pytest.mark.asyncio
    async def test_locked_conversation_lock_is_cleaned_after_release(self):
        conv_id = "conv-held-cleanup"
        lock = await agent._get_conversation_lock(conv_id)

        async with lock:
            agent._cleanup_conversation_related_state(conv_id)
            assert agent._conversation_locks.get(conv_id) is lock

        for _ in range(20):
            if conv_id not in agent._conversation_locks:
                break
            await asyncio.sleep(0)

        assert conv_id not in agent._conversation_locks

    @pytest.mark.asyncio
    async def test_cleanup_evicts_unlocked_lock(self):
        lock = await agent._get_conversation_lock("conv-free")

        assert agent._conversation_locks.get("conv-free") is lock

        agent._cleanup_conversation_related_state("conv-free")

        assert "conv-free" not in agent._conversation_locks
