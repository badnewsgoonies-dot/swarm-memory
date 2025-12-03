#!/usr/bin/env bash
#
# mem-log.sh - Log ATTEMPT, RESULT, and LESSON records linked to tasks
#
# Usage:
#   ./mem-log.sh attempt --task <task_id> --text "<text>" [options]
#   ./mem-log.sh result --task <task_id> --success <true|false> [options]
#   ./mem-log.sh lesson --text "<text>" [--task <task_id>] [--topic <topic>]
#   ./mem-log.sh history <task_id>
#
# Attempt options:
#   --task <task_id>    The TODO/GOAL ID this attempt is for
#   --text "<text>"     Description of what was tried
#   --source <source>   Who made the attempt (worker_codex, worker_ollama, etc.)
#
# Result options:
#   --task <task_id>    The TODO/GOAL ID this result is for
#   --success <bool>    true or false
#   --metric "<metric>" Numeric metric (e.g., "tests_passed=12/12", "score=0.85")
#   --text "<text>"     Optional description
#   --source <source>   Who reported the result
#
# Lesson options:
#   --text "<text>"     The lesson learned
#   --task <task_id>    Optional: link to a specific task
#   --topic <topic>     Topic/project tag
#   --source <source>   Who learned the lesson
#
# Examples:
#   ./mem-log.sh attempt --task vv-001 --text "Used pure similarity search"
#   ./mem-log.sh result --task vv-001 --success true --metric "tests_passed=12/12"
#   ./mem-log.sh result --task vv-001 --success false --text "3 tests failed on edge cases"
#   ./mem-log.sh lesson --topic VV --text "For VV tasks, always combine similarity + time decay"
#   ./mem-log.sh lesson --task vv-001 --text "Edge cases need separate test suite"
#   ./mem-log.sh history vv-001

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_FILE="${MEMORY_DB:-$SCRIPT_DIR/memory.db}"

# Detect Python
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "ERROR: No python found. Python is required." >&2
    exit 1
fi

cmd_attempt() {
    # Parse arguments
    local task_id="" text="" source="" topic=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --task) task_id="$2"; shift 2 ;;
            --text) text="$2"; shift 2 ;;
            --source) source="$2"; shift 2 ;;
            --topic) topic="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    # Validate required fields
    if [[ -z "$task_id" ]]; then
        echo "ERROR: --task is required" >&2
        exit 1
    fi
    if [[ -z "$text" ]]; then
        echo "ERROR: --text is required" >&2
        exit 1
    fi

    # Build and execute mem-db.sh write command
    local args=("$SCRIPT_DIR/mem-db.sh" "write" "t=M" "task_id=$task_id")
    [[ -n "$topic" ]] && args+=("topic=$topic")
    [[ -n "$source" ]] && args+=("source=$source")
    args+=("text=$text")

    # Execute
    "${args[@]}"

    echo ""
    echo "Logged ATTEMPT for task: $task_id"
}

cmd_result() {
    # Parse arguments
    local task_id="" success="" metric="" text="" source="" topic=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --task) task_id="$2"; shift 2 ;;
            --success) success="$2"; shift 2 ;;
            --metric) metric="$2"; shift 2 ;;
            --text) text="$2"; shift 2 ;;
            --source) source="$2"; shift 2 ;;
            --topic) topic="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    # Validate required fields
    if [[ -z "$task_id" ]]; then
        echo "ERROR: --task is required" >&2
        exit 1
    fi
    if [[ -z "$success" ]]; then
        echo "ERROR: --success is required (true or false)" >&2
        exit 1
    fi

    # Normalize success value
    local choice=""
    if [[ "$success" == "true" || "$success" == "1" || "$success" == "yes" ]]; then
        choice="success"
    else
        choice="failure"
    fi

    # Default text if not provided
    if [[ -z "$text" ]]; then
        text="Result: $choice"
        [[ -n "$metric" ]] && text+=" ($metric)"
    fi

    # Build and execute mem-db.sh write command
    local args=("$SCRIPT_DIR/mem-db.sh" "write" "t=R" "task_id=$task_id" "choice=$choice")
    [[ -n "$metric" ]] && args+=("metric=$metric")
    [[ -n "$topic" ]] && args+=("topic=$topic")
    [[ -n "$source" ]] && args+=("source=$source")
    args+=("text=$text")

    # Execute
    "${args[@]}"

    echo ""
    if [[ "$choice" == "success" ]]; then
        echo -e "\033[32mLogged SUCCESS for task: $task_id\033[0m"
    else
        echo -e "\033[31mLogged FAILURE for task: $task_id\033[0m"
    fi
}

cmd_lesson() {
    # Parse arguments
    local task_id="" text="" topic="" source=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --task) task_id="$2"; shift 2 ;;
            --text) text="$2"; shift 2 ;;
            --topic) topic="$2"; shift 2 ;;
            --source) source="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    # Validate required fields
    if [[ -z "$text" ]]; then
        echo "ERROR: --text is required" >&2
        exit 1
    fi

    # Build and execute mem-db.sh write command
    local args=("$SCRIPT_DIR/mem-db.sh" "write" "t=L")
    [[ -n "$task_id" ]] && args+=("task_id=$task_id")
    [[ -n "$topic" ]] && args+=("topic=$topic")
    [[ -n "$source" ]] && args+=("source=$source")
    args+=("text=$text")

    # Execute
    "${args[@]}"

    echo ""
    echo "Logged LESSON"
    [[ -n "$task_id" ]] && echo "  Linked to task: $task_id"
    [[ -n "$topic" ]] && echo "  Topic: $topic"
}

cmd_history() {
    local task_id="$1"

    if [[ -z "$task_id" ]]; then
        echo "ERROR: task ID required" >&2
        exit 1
    fi

    $PYTHON_BIN - "$DB_FILE" "$task_id" <<'PYEOF'
import sys
import sqlite3
import json

db_path = sys.argv[1]
task_id = sys.argv[2]

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# First, find the task itself
cursor.execute("""
SELECT id, anchor_type, anchor_topic, text, anchor_choice, timestamp
FROM chunks
WHERE anchor_type IN ('T', 'G')
  AND (links LIKE ? OR id = ?)
ORDER BY timestamp DESC
LIMIT 1
""", (f'%"id":"{task_id}"%', task_id if task_id.isdigit() else -1))

task_row = cursor.fetchone()

if task_row:
    db_id, glyph_type, topic, text, status, ts = task_row
    type_name = 'TODO' if glyph_type == 'T' else 'GOAL'
    print(f"\033[1;36m{type_name}: {task_id}\033[0m")
    print(f"  Status: {status or 'OPEN'}")
    print(f"  {text}")
    print()
else:
    print(f"\033[33mTask not found in database: {task_id}\033[0m")
    print()

# Now find all ATTEMPTs, RESULTs, and LESSONs linked to this task
cursor.execute("""
SELECT anchor_type, text, anchor_choice, metric, timestamp, anchor_source
FROM chunks
WHERE task_id = ?
ORDER BY timestamp ASC
""", (task_id,))

rows = cursor.fetchall()
conn.close()

if not rows:
    print("No history entries found.")
    sys.exit(0)

# Type markers and colors
type_info = {
    'M': ('ATTEMPT', '\033[1;34m'),   # Blue
    'R': ('RESULT', '\033[1;35m'),    # Magenta
    'L': ('LESSON', '\033[1;33m')     # Yellow
}

print(f"=== History ({len(rows)} entries) ===")
print()

for row in rows:
    glyph_type, text, choice, metric, ts, source = row
    type_name, color = type_info.get(glyph_type, (glyph_type, '\033[0m'))

    # Format timestamp
    ts_short = ts[:16] if ts else '?'

    print(f"{color}[{type_name}]\033[0m {ts_short}", end="")
    if source:
        print(f" \033[90m({source})\033[0m", end="")
    print()

    # For results, show success/failure
    if glyph_type == 'R':
        if choice == 'success':
            print(f"  \033[32mSUCCESS\033[0m", end="")
        else:
            print(f"  \033[31mFAILURE\033[0m", end="")
        if metric:
            print(f" | metric: {metric}", end="")
        print()

    print(f"  {text}")
    print()
PYEOF
}

show_help() {
    head -35 "$0" | tail -33
}

main() {
    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        attempt) cmd_attempt "$@" ;;
        result) cmd_result "$@" ;;
        lesson) cmd_lesson "$@" ;;
        history) cmd_history "$@" ;;
        help|--help|-h) show_help ;;
        *) echo "Unknown command: $cmd" >&2; show_help; exit 1 ;;
    esac
}

main "$@"
