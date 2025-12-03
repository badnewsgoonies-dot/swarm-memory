#!/bin/bash
# daemon-watch.sh - Live dashboard for daemon monitoring
# Usage: ./daemon-watch.sh [refresh_seconds]

REFRESH=${1:-2}
LOG_FILE="daemon.log"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

show_dashboard() {
    clear

    # Header
    echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${CYAN}                    DAEMON WATCH - $(date '+%H:%M:%S')${NC}"
    echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""

    # Status row
    DAEMON_PID=$(pgrep -f "swarm_daemon.py" | head -1)
    if [ -n "$DAEMON_PID" ]; then
        echo -e "${GREEN}● DAEMON RUNNING${NC} (PID: $DAEMON_PID)"
    else
        echo -e "${RED}○ DAEMON STOPPED${NC}"
    fi

    # Memory status
    MEM_STATUS=$(./mem-db.sh status 2>/dev/null | grep "Chunks:" | head -1)
    echo -e "${BLUE}Memory:${NC} ${MEM_STATUS:-N/A}"

    # Latest iteration
    if [ -f "$LOG_FILE" ]; then
        ITERATION=$(grep "Iteration" "$LOG_FILE" | tail -1 | grep -oP "Iteration \d+" || echo "N/A")
        echo -e "${YELLOW}Current:${NC} $ITERATION"
    fi
    echo ""

    # Divider
    echo -e "${CYAN}───────────────────── RECENT ACTIVITY ─────────────────────────${NC}"
    echo ""

    # Recent log entries (key events only)
    if [ -f "$LOG_FILE" ]; then
        tail -50 "$LOG_FILE" | grep -E "Iteration|Executing action|Governor|ERROR|write_memory|exec|done" | tail -15 | while read line; do
            if echo "$line" | grep -q "ERROR"; then
                echo -e "${RED}$line${NC}"
            elif echo "$line" | grep -q "Executing"; then
                echo -e "${GREEN}$line${NC}"
            elif echo "$line" | grep -q "Iteration"; then
                echo -e "${YELLOW}$line${NC}"
            else
                echo "$line"
            fi
        done
    else
        echo "No log file found"
    fi

    echo ""
    echo -e "${CYAN}───────────────────── LATEST LOG LINES ────────────────────────${NC}"
    echo ""

    # Raw last 10 lines
    if [ -f "$LOG_FILE" ]; then
        tail -10 "$LOG_FILE" | cut -c1-100
    fi

    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "Press ${BOLD}Ctrl+C${NC} to exit | Refresh: ${REFRESH}s | Log: $LOG_FILE"
}

# Main loop
trap "echo ''; echo 'Exiting...'; exit 0" INT

while true; do
    show_dashboard
    sleep "$REFRESH"
done
