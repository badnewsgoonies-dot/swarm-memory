#!/usr/bin/env bash
#
# watch-agents.sh - Real-time monitoring for daemon/worker agents
#
# Usage:
#   ./watch-agents.sh [mode] [options]
#
# Modes:
#   tail           Tail anchors.jsonl raw stream
#   attempts       Watch ATTEMPT glyphs (last 5 mins)
#   results        Watch RESULT glyphs (last 5 mins)
#   lessons        Watch LESSON glyphs (last 30 mins)
#   phases         Watch PHASE glyphs (last 10 mins)
#   task <id>      Watch specific task history
#   daemon [log]   Tail daemon log file
#   all            Dashboard view with multiple panels (requires tmux)
#
# Options:
#   -n <secs>      Refresh interval for watch modes (default: 3)
#   -l <limit>     Limit number of entries (default: 10)
#   -r <time>      Recent time filter (default varies by mode)
#
# Examples:
#   ./watch-agents.sh attempts -n 2
#   ./watch-agents.sh task vv2-001
#   ./watch-agents.sh phases -l 20
#   ./watch-agents.sh daemon /tmp/orch-test.log
#   ./watch-agents.sh all
#
# Description:
#   Provides various views into the agent/daemon system for real-time monitoring.
#   Great for watching agents "think + act" as they execute objectives.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANCHORS_FILE="$SCRIPT_DIR/anchors.jsonl"
MEM_DB="$SCRIPT_DIR/mem-db.sh"
MEM_LOG="$SCRIPT_DIR/mem-log.sh"
DAEMON_LOG="${DAEMON_LOG:-$SCRIPT_DIR/daemon.log}"

# Defaults
REFRESH_INTERVAL=3
LIMIT=10
RECENT=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

show_help() {
    head -35 "$0" | tail -33
    echo ""
    echo "Tips:"
    echo "  - Run 'watch-agents.sh all' for a tmux dashboard"
    echo "  - Use multiple terminals with different modes for custom monitoring"
    echo "  - Combine with 'tail -f daemon.log' for full context"
}

cmd_tail() {
    echo -e "${CYAN}${BOLD}=== Raw Anchors Stream ===${NC}"
    echo "Tailing: $ANCHORS_FILE"
    echo ""
    tail -f "$ANCHORS_FILE" | while IFS= read -r line; do
        # Pretty-print JSON lines with jq if available
        if command -v jq &>/dev/null; then
            echo "$line" | jq -C '.' 2>/dev/null || echo "$line"
        else
            echo "$line"
        fi
    done
}

cmd_attempts() {
    local recent="${RECENT:-5m}"
    echo -e "${BLUE}${BOLD}=== ATTEMPT Glyphs (recent=$recent, refresh=${REFRESH_INTERVAL}s) ===${NC}"
    watch -c -n "$REFRESH_INTERVAL" "$MEM_DB query t=M recent=$recent limit=$LIMIT"
}

cmd_results() {
    local recent="${RECENT:-5m}"
    echo -e "${MAGENTA}${BOLD}=== RESULT Glyphs (recent=$recent, refresh=${REFRESH_INTERVAL}s) ===${NC}"
    watch -c -n "$REFRESH_INTERVAL" "$MEM_DB query t=R recent=$recent limit=$LIMIT"
}

cmd_lessons() {
    local recent="${RECENT:-30m}"
    echo -e "${YELLOW}${BOLD}=== LESSON Glyphs (recent=$recent, refresh=${REFRESH_INTERVAL}s) ===${NC}"
    watch -c -n "$REFRESH_INTERVAL" "$MEM_DB query t=L recent=$recent limit=$LIMIT"
}

cmd_phases() {
    local recent="${RECENT:-10m}"
    echo -e "${GREEN}${BOLD}=== PHASE Glyphs (recent=$recent, refresh=${REFRESH_INTERVAL}s) ===${NC}"
    watch -c -n "$REFRESH_INTERVAL" "$MEM_DB query t=P recent=$recent limit=$LIMIT"
}

cmd_task() {
    local task_id="$1"
    if [[ -z "$task_id" ]]; then
        echo -e "${RED}ERROR: task ID required${NC}" >&2
        echo "Usage: $0 task <task_id>"
        exit 1
    fi
    echo -e "${CYAN}${BOLD}=== Task History: $task_id (refresh=${REFRESH_INTERVAL}s) ===${NC}"
    watch -c -n "$REFRESH_INTERVAL" "$MEM_LOG history $task_id"
}

cmd_daemon() {
    local log_file="${1:-$DAEMON_LOG}"
    if [[ ! -f "$log_file" ]]; then
        echo -e "${RED}ERROR: Log file not found: $log_file${NC}" >&2
        echo "Usage: $0 daemon [/path/to/log]"
        echo "Default: $DAEMON_LOG"
        exit 1
    fi
    echo -e "${CYAN}${BOLD}=== Daemon Log: $log_file ===${NC}"
    tail -f "$log_file"
}

cmd_actions() {
    # Watch recent daemon actions (t=a)
    local recent="${RECENT:-5m}"
    echo -e "${CYAN}${BOLD}=== Daemon Actions (recent=$recent, refresh=${REFRESH_INTERVAL}s) ===${NC}"
    watch -c -n "$REFRESH_INTERVAL" "$MEM_DB query t=a topic=daemon recent=$recent limit=$LIMIT"
}

cmd_todos() {
    # Watch TODO status changes
    echo -e "${CYAN}${BOLD}=== TODO Status (refresh=${REFRESH_INTERVAL}s) ===${NC}"
    watch -c -n "$REFRESH_INTERVAL" "$SCRIPT_DIR/mem-todo.sh list"
}

cmd_all() {
    # Create a tmux dashboard with multiple panes
    if ! command -v tmux &>/dev/null; then
        echo -e "${RED}ERROR: tmux required for dashboard mode${NC}" >&2
        echo "Install with: sudo apt install tmux"
        echo "Or run individual watch modes in separate terminals."
        exit 1
    fi

    local session="agent-monitor"

    # Kill existing session if it exists
    tmux kill-session -t "$session" 2>/dev/null || true

    # Create new session with 4-pane layout
    # Layout:
    # +------------------+------------------+
    # |     PHASES       |     RESULTS      |
    # +------------------+------------------+
    # |    ATTEMPTS      |   DAEMON LOG     |
    # +------------------+------------------+

    tmux new-session -d -s "$session" -n "agents"

    # Pane 0: PHASES (top-left)
    tmux send-keys -t "$session" "cd '$SCRIPT_DIR' && watch -c -n 3 './mem-db.sh query t=P recent=10m limit=8'" Enter

    # Split horizontally for RESULTS (top-right)
    tmux split-window -h -t "$session"
    tmux send-keys -t "$session" "cd '$SCRIPT_DIR' && watch -c -n 3 './mem-db.sh query t=R recent=5m limit=8'" Enter

    # Split vertically on left for ATTEMPTS (bottom-left)
    tmux select-pane -t "$session:0.0"
    tmux split-window -v -t "$session"
    tmux send-keys -t "$session" "cd '$SCRIPT_DIR' && watch -c -n 3 './mem-db.sh query t=M recent=5m limit=8'" Enter

    # Split vertically on right for DAEMON LOG (bottom-right)
    tmux select-pane -t "$session:0.1"
    tmux split-window -v -t "$session"
    tmux send-keys -t "$session" "tail -f '$DAEMON_LOG' 2>/dev/null || echo 'No daemon.log yet. Start a daemon to see output.'" Enter

    # Attach to session
    echo -e "${GREEN}${BOLD}Launching tmux dashboard...${NC}"
    echo "Detach: Ctrl+B then D | Kill: Ctrl+B then :kill-session"
    sleep 1
    tmux attach -t "$session"
}

cmd_orch() {
    # Watch orchestration for a specific task
    local task_id="${1:-}"
    if [[ -z "$task_id" ]]; then
        echo -e "${YELLOW}Watching all recent orchestration activity...${NC}"
        watch -c -n "$REFRESH_INTERVAL" "$MEM_DB query t=P recent=30m limit=$LIMIT"
    else
        echo -e "${CYAN}${BOLD}=== Orchestration: $task_id (refresh=${REFRESH_INTERVAL}s) ===${NC}"
        watch -c -n "$REFRESH_INTERVAL" "$MEM_DB query t=P task_id=$task_id recent=2h limit=$LIMIT"
    fi
}

parse_opts() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -n) REFRESH_INTERVAL="$2"; shift 2 ;;
            -l) LIMIT="$2"; shift 2 ;;
            -r) RECENT="$2"; shift 2 ;;
            -h|--help) show_help; exit 0 ;;
            *) break ;;
        esac
    done
}

main() {
    local cmd="${1:-help}"
    shift || true

    # Parse remaining options
    case "$cmd" in
        tail) cmd_tail ;;
        attempts|attempt|atts) parse_opts "$@"; cmd_attempts ;;
        results|result|res) parse_opts "$@"; cmd_results ;;
        lessons|lesson|les) parse_opts "$@"; cmd_lessons ;;
        phases|phase|ph) parse_opts "$@"; cmd_phases ;;
        task) parse_opts "$@"; cmd_task "$@" ;;
        daemon|log) cmd_daemon "$@" ;;
        actions|acts) parse_opts "$@"; cmd_actions ;;
        todos|todo) parse_opts "$@"; cmd_todos ;;
        orch|orchestration) parse_opts "$@"; cmd_orch "$@" ;;
        all|dashboard) cmd_all ;;
        help|--help|-h) show_help ;;
        *) echo -e "${RED}Unknown mode: $cmd${NC}" >&2; show_help; exit 1 ;;
    esac
}

main "$@"
