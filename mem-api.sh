#!/usr/bin/env bash
#
# mem-api.sh - Control the memory API server
#
# Usage:
#   ./mem-api.sh start       # Start server on port 8765
#   ./mem-api.sh stop        # Stop server
#   ./mem-api.sh status      # Check if running
#   ./mem-api.sh restart     # Restart server
#   ./mem-api.sh logs        # Show recent logs
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_SCRIPT="$SCRIPT_DIR/mem-server.py"
PID_FILE="$SCRIPT_DIR/.mem-server.pid"
LOG_FILE="$SCRIPT_DIR/mem-server.log"
PORT="${MEM_API_PORT:-8765}"
HOST="${MEM_API_HOST:-0.0.0.0}"

cmd_start() {
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Server already running (PID: $PID)"
            return 0
        fi
        rm -f "$PID_FILE"
    fi

    echo "Starting memory API server on $HOST:$PORT..."
    nohup python3 "$SERVER_SCRIPT" --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    sleep 1
    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Server started (PID: $(cat "$PID_FILE"))"
        echo "Logs: $LOG_FILE"
        echo ""
        echo "Endpoints:"
        echo "  http://$HOST:$PORT/health"
        echo "  http://$HOST:$PORT/briefing"
        echo "  http://$HOST:$PORT/query?t=d&limit=5"
        echo "  http://$HOST:$PORT/write (POST)"
    else
        echo "Failed to start server. Check logs: $LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

cmd_stop() {
    if [[ ! -f "$PID_FILE" ]]; then
        echo "Server not running (no PID file)"
        return 0
    fi

    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping server (PID: $PID)..."
        kill "$PID"
        sleep 1
        if kill -0 "$PID" 2>/dev/null; then
            echo "Force killing..."
            kill -9 "$PID" 2>/dev/null || true
        fi
        echo "Server stopped"
    else
        echo "Server not running (stale PID)"
    fi
    rm -f "$PID_FILE"
}

cmd_status() {
    if [[ ! -f "$PID_FILE" ]]; then
        echo "Server not running"
        return 1
    fi

    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Server running (PID: $PID)"
        echo "Testing health endpoint..."
        curl -s "http://localhost:$PORT/health" 2>/dev/null || echo "  (health check failed)"
        return 0
    else
        echo "Server not running (stale PID file)"
        rm -f "$PID_FILE"
        return 1
    fi
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_logs() {
    if [[ -f "$LOG_FILE" ]]; then
        tail -50 "$LOG_FILE"
    else
        echo "No log file found"
    fi
}

main() {
    local cmd="${1:-help}"
    case "$cmd" in
        start)   cmd_start ;;
        stop)    cmd_stop ;;
        status)  cmd_status ;;
        restart) cmd_restart ;;
        logs)    cmd_logs ;;
        *)
            echo "Usage: $0 {start|stop|status|restart|logs}"
            echo ""
            echo "Environment variables:"
            echo "  MEM_API_PORT  - Port to listen on (default: 8765)"
            echo "  MEM_API_HOST  - Host to bind to (default: 0.0.0.0)"
            ;;
    esac
}

main "$@"
