#!/bin/bash
set -euo pipefail

cd /home/site/wwwroot
export PYTHONPATH=/home/site/wwwroot:/home/site/wwwroot/antenv/lib/python3.12/site-packages:$PYTHONPATH
export UPLOAD_INLINE_WORKER_RUNTIME_ENABLED="${UPLOAD_INLINE_WORKER_RUNTIME_ENABLED:-false}"

RUN_DIR="${WORKER_RUN_DIR:-/home/site/wwwroot/run}"
mkdir -p "$RUN_DIR" /home/LogFiles

UPLOAD_PID_FILE="${UPLOAD_WORKER_PID_FILE:-$RUN_DIR/upload-worker.pid}"
UPLOAD_SUPERVISOR_PID_FILE="${UPLOAD_WORKER_SUPERVISOR_PID_FILE:-$RUN_DIR/upload-worker-supervisor.pid}"
EXPORT_PID_FILE="${EXPORT_WORKER_PID_FILE:-$RUN_DIR/export-worker.pid}"
EXPORT_SUPERVISOR_PID_FILE="${EXPORT_WORKER_SUPERVISOR_PID_FILE:-$RUN_DIR/export-worker-supervisor.pid}"

start_supervised_worker() {
  local name="$1"
  local log_file="$2"
  local pid_file="$3"
  local supervisor_pid_file="$4"
  shift 4

  (
    echo "$$" > "$supervisor_pid_file"
    while true; do
      "$@" >> "$log_file" 2>&1 &
      local worker_pid=$!
      echo "$worker_pid" > "$pid_file"

      set +e
      wait "$worker_pid"
      local exit_code=$?
      set -e

      echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [$name] exited with code $exit_code; restarting in 2s" >> "$log_file"
      sleep 2
    done
  ) &
}

SIDE_CAR_ENABLED="$(echo "${UPLOAD_DEDICATED_WORKER_ENABLED:-true}" | tr '[:upper:]' '[:lower:]')"
if [[ "$SIDE_CAR_ENABLED" == "true" ]]; then
  export UPLOAD_DEDICATED_WORKER_ENABLED="true"
  export UPLOAD_WORKER_INSTANCE_ID="${UPLOAD_WORKER_INSTANCE_ID:-worker-sidecar-${WEBSITE_INSTANCE_ID:-local}}"
  echo "Starting dedicated upload worker sidecar with supervisor (${UPLOAD_WORKER_INSTANCE_ID})..."
  start_supervised_worker \
    "upload-worker" \
    "/home/LogFiles/upload-worker.log" \
    "$UPLOAD_PID_FILE" \
    "$UPLOAD_SUPERVISOR_PID_FILE" \
    python upload_worker.py --batch-size "${UPLOAD_WORKER_BATCH_SIZE:-4}" --poll-seconds "${UPLOAD_WORKER_POLL_SECONDS:-2.5}"
else
  export UPLOAD_DEDICATED_WORKER_ENABLED="false"
  echo "Dedicated upload worker sidecar disabled."
fi

EXPORT_SIDE_CAR_ENABLED="$(echo "${EXPORT_DEDICATED_WORKER_ENABLED:-true}" | tr '[:upper:]' '[:lower:]')"
if [[ "$EXPORT_SIDE_CAR_ENABLED" == "true" ]]; then
  export EXPORT_DEDICATED_WORKER_ENABLED="true"
  export EXPORT_WORKER_INSTANCE_ID="${EXPORT_WORKER_INSTANCE_ID:-export-worker-sidecar-${WEBSITE_INSTANCE_ID:-local}}"
  echo "Starting dedicated export worker sidecar with supervisor (${EXPORT_WORKER_INSTANCE_ID})..."
  start_supervised_worker \
    "export-worker" \
    "/home/LogFiles/export-worker.log" \
    "$EXPORT_PID_FILE" \
    "$EXPORT_SUPERVISOR_PID_FILE" \
    python export_worker.py --batch-size "${EXPORT_WORKER_BATCH_SIZE:-3}" --poll-seconds "${EXPORT_WORKER_POLL_SECONDS:-2.0}"
else
  export EXPORT_DEDICATED_WORKER_ENABLED="false"
  echo "Dedicated export worker sidecar disabled."
fi

echo "Starting DBDE AI Agent v7.3.0..."
exec python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
