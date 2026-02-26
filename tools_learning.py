# =============================================================================
# tools_learning.py — Writer profile persistence helpers
# =============================================================================

import json
import logging
from datetime import datetime, timezone

from storage import table_query, table_insert, table_merge

_WRITER_PROFILE_PARTITION = "writer"

def _normalize_author(author_name: str) -> str:
    return " ".join((author_name or "").strip().lower().split())

def _writer_profile_row_key(author_name: str) -> str:
    base = _normalize_author(author_name)
    if not base:
        return ""
    safe = (
        base.replace("/", "_")
        .replace("\\", "_")
        .replace("#", "_")
        .replace("?", "_")
        .replace("'", "_")
        .replace('"', "_")
    )
    return safe[:120]

async def _save_writer_profile(
    author_name: str,
    analysis: str,
    sample_ids=None,
    sample_count: int = 0,
    topic: str = "",
    work_item_type: str = "User Story",
    preferred_vocabulary: str = "",
    title_pattern: str = "",
    ac_structure: str = "",
) -> bool:
    row_key = _writer_profile_row_key(author_name)
    if not row_key or not analysis:
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    entity = {
        "PartitionKey": _WRITER_PROFILE_PARTITION,
        "RowKey": row_key,
        "AuthorName": (author_name or "").strip()[:200],
        "AuthorLower": _normalize_author(author_name)[:200],
        "StyleAnalysis": analysis[:20000],
        "SampleCount": int(sample_count or 0),
        "SampleIdsJson": json.dumps((sample_ids or [])[:100], ensure_ascii=False),
        "Topic": (topic or "")[:200],
        "WorkItemType": (work_item_type or "User Story")[:80],
        "PreferredVocabulary": (preferred_vocabulary or "")[:2000],
        "TitlePattern": (title_pattern or "")[:500],
        "ACStructure": (ac_structure or "")[:1000],
        "UpdatedAt": now_iso,
    }

    try:
        existing = await table_query(
            "WriterProfiles",
            f"PartitionKey eq '{_WRITER_PROFILE_PARTITION}' and RowKey eq '{row_key}'",
            top=1,
        )
        if existing:
            await table_merge("WriterProfiles", entity)
        else:
            entity["CreatedAt"] = now_iso
            inserted = await table_insert("WriterProfiles", entity)
            if not inserted:
                logging.error("[Tools] _save_writer_profile insert returned False")
                return False
        return True
    except Exception as e:
        logging.error("[Tools] _save_writer_profile failed: %s", e)
        return False

async def _load_writer_profile(author_name: str):
    row_key = _writer_profile_row_key(author_name)
    if not row_key:
        return None

    try:
        rows = await table_query(
            "WriterProfiles",
            f"PartitionKey eq '{_WRITER_PROFILE_PARTITION}' and RowKey eq '{row_key}'",
            top=1,
        )
        if not rows:
            return None
        row = rows[0]
        sample_ids = []
        raw_ids = row.get("SampleIdsJson", "[]")
        try:
            sample_ids = json.loads(raw_ids) if raw_ids else []
        except Exception as e:
            logging.warning("[Tools] _load_writer_profile sample ids parse failed: %s", e)
        return {
            "author_name": row.get("AuthorName", author_name),
            "style_analysis": row.get("StyleAnalysis", ""),
            "sample_count": int(row.get("SampleCount", 0) or 0),
            "sample_ids": sample_ids if isinstance(sample_ids, list) else [],
            "topic": row.get("Topic", ""),
            "work_item_type": row.get("WorkItemType", "User Story"),
            "preferred_vocabulary": row.get("PreferredVocabulary", ""),
            "title_pattern": row.get("TitlePattern", ""),
            "ac_structure": row.get("ACStructure", ""),
            "updated_at": row.get("UpdatedAt", ""),
        }
    except Exception as e:
        logging.error("[Tools] _load_writer_profile failed: %s", e)
        return None
