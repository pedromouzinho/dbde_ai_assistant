#!/bin/bash
cd /home/site/wwwroot
export PYTHONPATH=/home/site/wwwroot:/home/site/wwwroot/antenv/lib/python3.12/site-packages:$PYTHONPATH
echo "Starting DBDE AI Agent v7.0..."
exec python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
