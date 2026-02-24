#!/usr/bin/env python3
"""Export worker desacoplado para processar ExportJobs fora do request web."""

import argparse
import asyncio
import logging
import os
import uuid

import httpx

os.environ.setdefault("EXPORT_WORKER_INSTANCE_ID", f"export-worker-{uuid.uuid4().hex[:8]}")
os.environ.setdefault("UPLOAD_INLINE_WORKER_RUNTIME_ENABLED", "false")

from config import EXPORT_WORKER_BATCH_SIZE, EXPORT_WORKER_POLL_SECONDS
from storage import init_http_client, ensure_tables_exist, ensure_blob_containers
from app import process_export_jobs_once


logger = logging.getLogger("export_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def run_worker(once: bool, batch_size: int, poll_seconds: float) -> None:
    client = httpx.AsyncClient(timeout=60)
    init_http_client(client)
    try:
        await ensure_tables_exist()
        await ensure_blob_containers()
        if once:
            result = await process_export_jobs_once(max_jobs=batch_size)
            logger.info("run-once result=%s", result)
            return

        logger.info("export worker loop started (batch_size=%s poll=%.1fs)", batch_size, poll_seconds)
        while True:
            try:
                result = await process_export_jobs_once(max_jobs=batch_size)
                if result.get("processed", 0) > 0:
                    logger.info(
                        "processed=%s claimed=%s skipped=%s",
                        result.get("processed"),
                        result.get("claimed"),
                        result.get("skipped"),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("export worker iteration failed: %s", e)
            await asyncio.sleep(max(0.5, poll_seconds))
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="DBDE export worker")
    parser.add_argument("--once", action="store_true", help="processa apenas um ciclo")
    parser.add_argument("--batch-size", type=int, default=EXPORT_WORKER_BATCH_SIZE)
    parser.add_argument("--poll-seconds", type=float, default=EXPORT_WORKER_POLL_SECONDS)
    args = parser.parse_args()
    asyncio.run(run_worker(args.once, max(1, args.batch_size), max(0.5, args.poll_seconds)))


if __name__ == "__main__":
    main()

