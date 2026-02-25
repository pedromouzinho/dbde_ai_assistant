"""
Rate limit storage backend usando Azure Table Storage.
Compatível com middleware async do FastAPI.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from storage import table_query, table_insert, table_merge
from utils import create_logged_task

logger = logging.getLogger(__name__)

RATE_LIMIT_TABLE = "RateLimits"


class TableStorageRateLimit:
    """
    Sliding-window rate limiter backed by Azure Table Storage.
    Usa cache local para reduzir round-trips e sincroniza best-effort no storage.
    """

    def __init__(self):
        self._local_cache = {}  # {cache_key: count}
        self._lock = asyncio.Lock()

    async def is_rate_limited(self, key: str, limit: int, window_seconds: int) -> bool:
        """
        Retorna True se o key excedeu o limit na janela actual.
        """
        if limit <= 0 or window_seconds <= 0:
            return False

        now = time.time()
        window_start = int(now / window_seconds) * window_seconds
        cache_key = f"{key}:{window_start}"

        async with self._lock:
            # Fast path: cache local desta instância.
            cached_count = self._local_cache.get(cache_key)
            if cached_count is not None:
                next_count = cached_count + 1
                self._local_cache[cache_key] = next_count
                limited = next_count > limit
                create_logged_task(
                    self._persist_count(key, int(window_start), next_count),
                    name="rate_limit_persist_fast",
                )
                return limited

            # Slow path: consultar storage DENTRO do lock para evitar corridas.
            count = 0
            try:
                filter_expr = f"PartitionKey eq '{key}' and RowKey eq '{int(window_start)}'"
                rows = await table_query(RATE_LIMIT_TABLE, filter_expr, top=1)
                if rows:
                    count = int(rows[0].get("Count", 0) or 0)
            except Exception as e:
                logger.warning("[RateLimit] Table read failed, allowing request: %s", e)
                count = 0

            next_count = count + 1
            self._local_cache[cache_key] = next_count

            limited = next_count > limit
            create_logged_task(
                self._persist_count(key, int(window_start), next_count),
                name="rate_limit_persist_slow",
            )
            return limited

    async def _persist_count(self, key: str, window_start: int, count: int):
        """Persist count no Table Storage (best-effort)."""
        entity = {
            "PartitionKey": key,
            "RowKey": str(window_start),
            "Count": count,
            "UpdatedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await table_merge(RATE_LIMIT_TABLE, entity)
        except Exception:
            try:
                await table_insert(RATE_LIMIT_TABLE, entity)
            except Exception as e:
                logger.warning("[RateLimit] Table write failed: %s", e)

    def cleanup_local_cache(self):
        """Remove janelas antigas do cache local."""
        now = time.time()
        expired = [k for k in self._local_cache if float(k.rsplit(":", 1)[-1]) < now - 300]
        for k in expired:
            self._local_cache.pop(k, None)
