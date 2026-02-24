#!/bin/bash
cd /home/site/wwwroot
export PYTHONPATH=/home/site/wwwroot:/home/site/wwwroot/antenv/lib/python3.12/site-packages:$PYTHONPATH
export UPLOAD_INLINE_WORKER_RUNTIME_ENABLED="${UPLOAD_INLINE_WORKER_RUNTIME_ENABLED:-false}"
SIDE_CAR_ENABLED="$(echo "${UPLOAD_DEDICATED_WORKER_ENABLED:-true}" | tr '[:upper:]' '[:lower:]')"
if [[ "$SIDE_CAR_ENABLED" == "true" ]]; then
  export UPLOAD_DEDICATED_WORKER_ENABLED="true"
  export UPLOAD_WORKER_INSTANCE_ID="${UPLOAD_WORKER_INSTANCE_ID:-worker-sidecar-${WEBSITE_INSTANCE_ID:-local}}"
  mkdir -p /home/LogFiles
  echo "Starting dedicated upload worker sidecar (${UPLOAD_WORKER_INSTANCE_ID})..."
  nohup python upload_worker.py \
    --batch-size "${UPLOAD_WORKER_BATCH_SIZE:-4}" \
    --poll-seconds "${UPLOAD_WORKER_POLL_SECONDS:-2.5}" \
    >> /home/LogFiles/upload-worker.log 2>&1 &
else
  export UPLOAD_DEDICATED_WORKER_ENABLED="false"
  echo "Dedicated upload worker sidecar disabled."
fi
EXPORT_SIDE_CAR_ENABLED="$(echo "${EXPORT_DEDICATED_WORKER_ENABLED:-true}" | tr '[:upper:]' '[:lower:]')"
if [[ "$EXPORT_SIDE_CAR_ENABLED" == "true" ]]; then
  export EXPORT_DEDICATED_WORKER_ENABLED="true"
  export EXPORT_WORKER_INSTANCE_ID="${EXPORT_WORKER_INSTANCE_ID:-export-worker-sidecar-${WEBSITE_INSTANCE_ID:-local}}"
  mkdir -p /home/LogFiles
  echo "Starting dedicated export worker sidecar (${EXPORT_WORKER_INSTANCE_ID})..."
  nohup python export_worker.py \
    --batch-size "${EXPORT_WORKER_BATCH_SIZE:-3}" \
    --poll-seconds "${EXPORT_WORKER_POLL_SECONDS:-2.0}" \
    >> /home/LogFiles/export-worker.log 2>&1 &
else
  export EXPORT_DEDICATED_WORKER_ENABLED="false"
  echo "Dedicated export worker sidecar disabled."
fi
echo "Starting DBDE AI Agent v7.2.1..."
exec python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
