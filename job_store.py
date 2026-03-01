"""
Job store backed by Azure Table Storage with local cache.
Substitui os dicts in-memory upload_jobs_store e export_jobs_store.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from storage import table_query, table_insert, table_merge
from utils import odata_escape

logger = logging.getLogger(__name__)


class PersistentJobStore:
    """
    Dict-like store com write-through para Table Storage.
    - Writes: local + Table Storage (async)
    - Reads: local first, Table Storage fallback
    """

    def __init__(self, table_name: str, partition_key: str = "job"):
        self._table = table_name
        self._default_partition_key = partition_key
        self._local: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def get(self, job_id: str, default=None) -> Optional[Dict]:
        return self._local.get(job_id, default)

    async def get_or_fetch(self, job_id: str, partition_key: Optional[str] = None) -> Optional[Dict]:
        """Get from local cache, fallback to Table Storage."""
        if job_id in self._local:
            return self._local[job_id]
        pk = str(partition_key or self._default_partition_key)
        try:
            safe_pk = odata_escape(pk)
            safe_job_id = odata_escape(job_id)
            filter_expr = f"PartitionKey eq '{safe_pk}' and RowKey eq '{safe_job_id}'"
            rows = await table_query(self._table, filter_expr, top=1)
            if rows:
                row = rows[0]
                payload = {}
                raw_payload = row.get("PayloadJson")
                if raw_payload:
                    try:
                        payload = json.loads(raw_payload)
                    except Exception:
                        payload = {}
                if isinstance(payload, dict) and payload:
                    self._local[job_id] = payload
                    return payload
        except Exception as e:
            logger.warning("[JobStore:%s] Table fetch failed for %s: %s", self._table, job_id, e)
        return None

    async def put(self, job_id: str, data: Dict[str, Any], partition_key: Optional[str] = None):
        """Write to local cache and Table Storage."""
        async with self._lock:
            self._local[job_id] = data

        pk = str(partition_key or self._default_partition_key)
        payload_json = json.dumps(data, ensure_ascii=False, default=str)
        if len(payload_json) > 30000:
            payload_json = json.dumps(
                {
                    "_truncated": True,
                    "job_id": job_id,
                    "status": str(data.get("status", "")),
                },
                ensure_ascii=False,
            )
        entity = {
            "PartitionKey": pk,
            "RowKey": job_id,
            "Status": str(data.get("status", ""))[:32],
            "UpdatedAt": datetime.now(timezone.utc).isoformat(),
            "PayloadJson": payload_json,
        }
        try:
            await table_merge(self._table, entity)
        except Exception:
            try:
                await table_insert(self._table, entity)
            except Exception as e:
                logger.warning("[JobStore:%s] Persist failed for %s: %s", self._table, job_id, e)

    def pop(self, job_id: str, default=None) -> Optional[Dict]:
        """Remove entry from LOCAL cache only. Does NOT delete from Table Storage.
        For full deletion, use table_delete() separately."""
        return self._local.pop(job_id, default)

    def items(self):
        return self._local.items()

    def values(self):
        return self._local.values()

    def __contains__(self, key):
        return key in self._local

    def __getitem__(self, key):
        return self._local[key]

    def __len__(self):
        return len(self._local)
