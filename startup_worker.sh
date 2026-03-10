#!/bin/bash
set -euo pipefail
cd /home/site/wwwroot
export PYTHONPATH=/home/site/wwwroot:/home/site/wwwroot/antenv/lib/python3.12/site-packages:$PYTHONPATH
echo "Starting DBDE upload worker..."
exec python upload_worker.py --batch-size "${UPLOAD_WORKER_BATCH_SIZE:-4}" --poll-seconds "${UPLOAD_WORKER_POLL_SECONDS:-2.5}"
