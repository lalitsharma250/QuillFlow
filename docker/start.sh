#!/bin/bash
set -e

echo "Starting QuillFlow..."

# Check environment to decide what to run
MODE="${STARTUP_MODE:-combined}"

case "$MODE" in
    "api")
        echo "Starting API only..."
        exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
        ;;
    "worker")
        echo "Starting worker only..."
        exec arq app.workers.settings.WorkerSettings
        ;;
    "combined")
        echo "Starting API + Worker..."
        arq app.workers.settings.WorkerSettings &
        WORKER_PID=$!
        exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
        ;;
    *)
        echo "Unknown STARTUP_MODE: $MODE"
        exit 1
        ;;
esac