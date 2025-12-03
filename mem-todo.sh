#!/usr/bin/env bash
#
# mem-todo.sh - Task-centric memory CLI for TODOs and GOALs
#
# Usage:
#   ./mem-todo.sh add --id <id> --topic <topic> --text "<text>" [options]
#   ./mem-todo.sh goal --id <id> --topic <topic> --text "<text>" [options]
#   ./mem-todo.sh list [--topic <topic>] [--status <status>]
#   ./mem-todo.sh get <id>
#   ./mem-todo.sh update <id> --status <status>
#   ./mem-todo.sh done <id>
#   ./mem-todo.sh block <id>
#
# Add options:
#   --id <id>           Task ID (e.g., "vv-001")
#   --topic <topic>     Project/topic tag
#   --text "<text>"     Task description (1-3 sentences)
#   --importance <H|M|L>  Priority level
#   --due <ISO date>    Due date
#   --source <source>   Who created it (user, manager, worker_codex, etc.)
#
# List options:
#   --topic <topic>     Filter by topic/project
#   --status <status>   Filter by status (OPEN, IN_PROGRESS, DONE, BLOCKED)
#   --limit <n>         Max results (default: 20)
#
# Examples:
#   ./mem-todo.sh add --id vv-001 --topic VV --text "Add edge-case tests" --importance H
#   ./mem-todo.sh goal --id vv-g01 --topic VV --text "Port vale-village to Preact"
#   ./mem-todo.sh list --topic VV
#   ./mem-todo.sh list --status OPEN
#   ./mem-todo.sh update vv-001 --status IN_PROGRESS
#   ./mem-todo.sh done vv-001
#   ./mem-todo.sh block vv-001

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

cmd_add() {
    local glyph_type="T"  # TODO by default
    [[ "${1:-}" == "goal" ]] && glyph_type="G" && shift

    # Parse arguments
    local id="" topic="" text="" importance="" due="" source=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --id) id="$2"; shift 2 ;;
            --topic) topic="$2"; shift 2 ;;
            --text) text="$2"; shift 2 ;;
            --importance) importance="$2"; shift 2 ;;
            --due) due="$2"; shift 2 ;;
            --source) source="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    # Validate required fields
    if [[ -z "$id" ]]; then
        echo "ERROR: --id is required" >&2
        exit 1
    fi
    if [[ -z "$text" ]]; then
        echo "ERROR: --text is required" >&2
        exit 1
    fi

    # Build and execute mem-db.sh write command
    local args=("$SCRIPT_DIR/mem-db.sh" "write" "t=$glyph_type" "choice=OPEN")
    [[ -n "$topic" ]] && args+=("topic=$topic")
    [[ -n "$importance" ]] && args+=("importance=$importance")
    [[ -n "$due" ]] && args+=("due=$due")
    [[ -n "$source" ]] && args+=("source=$source")
    # Store id in links field (JSON format)
    args+=("links={\"id\":\"$id\"}")
    args+=("text=$text")

    # Execute
    "${args[@]}"

    echo ""
    if [[ "$glyph_type" == "T" ]]; then
        echo "Created TODO: $id"
    else
        echo "Created GOAL: $id"
    fi
}

cmd_list() {
    # Parse arguments
    local topic="" status="" limit="20"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --topic) topic="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            --limit) limit="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    $PYTHON_BIN - "$DB_FILE" "$topic" "$status" "$limit" <<'PYEOF'
import sys
import sqlite3
import json

db_path = sys.argv[1]
topic_filter = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
status_filter = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
limit = int(sys.argv[4]) if len(sys.argv) > 4 else 20

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Build query for TODOs and GOALs
where_clauses = ["anchor_type IN ('T', 'G')"]
params = {}

if topic_filter:
    where_clauses.append("anchor_topic = :topic")
    params['topic'] = topic_filter

if status_filter:
    where_clauses.append("anchor_choice = :status")
    params['status'] = status_filter.upper()

query = f"""
SELECT id, anchor_type, anchor_topic, text, anchor_choice, importance, due, links, timestamp
FROM chunks
WHERE {' AND '.join(where_clauses)}
ORDER BY
    CASE anchor_choice
        WHEN 'BLOCKED' THEN 1
        WHEN 'IN_PROGRESS' THEN 2
        WHEN 'OPEN' THEN 3
        WHEN 'DONE' THEN 4
    END,
    CASE importance
        WHEN 'H' THEN 1
        WHEN 'M' THEN 2
        WHEN 'L' THEN 3
        ELSE 4
    END,
    timestamp DESC
LIMIT {limit}
"""

cursor.execute(query, params)
rows = cursor.fetchall()
conn.close()

if not rows:
    print("No tasks found.")
    sys.exit(0)

# Status colors
status_colors = {
    'OPEN': '\033[1;33m',       # Yellow
    'IN_PROGRESS': '\033[1;34m', # Blue
    'DONE': '\033[1;32m',       # Green
    'BLOCKED': '\033[1;31m'     # Red
}

# Type markers
type_markers = {'T': 'TODO', 'G': 'GOAL'}

for row in rows:
    db_id, glyph_type, topic, text, status, importance, due, links, ts = row

    # Extract task ID from links if stored there
    task_id = None
    if links:
        try:
            links_obj = json.loads(links)
            task_id = links_obj.get('id')
        except:
            pass

    type_marker = type_markers.get(glyph_type, '?')
    status = status or 'OPEN'
    status_color = status_colors.get(status, '\033[0m')
    topic = topic or '-'
    importance = importance or '-'

    # Format: [TYPE] [STATUS] id | topic | importance | text
    id_str = task_id or f"#{db_id}"
    print(f"\033[1;36m[{type_marker}]\033[0m {status_color}[{status}]\033[0m {id_str}")
    print(f"  \033[33m{topic}\033[0m | imp={importance}", end="")
    if due:
        print(f" | due={due[:10]}", end="")
    print()
    print(f"  {text}")
    print()
PYEOF
}

cmd_get() {
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

# Search by task_id in links JSON or by database ID
cursor.execute("""
SELECT id, anchor_type, anchor_topic, text, anchor_choice, importance, due, links, timestamp, anchor_source
FROM chunks
WHERE anchor_type IN ('T', 'G')
  AND (links LIKE ? OR id = ?)
ORDER BY timestamp DESC
LIMIT 1
""", (f'%"id":"{task_id}"%', task_id if task_id.isdigit() else -1))

row = cursor.fetchone()
conn.close()

if not row:
    print(f"Task not found: {task_id}")
    sys.exit(1)

db_id, glyph_type, topic, text, status, importance, due, links, ts, source = row

# Extract task ID
tid = None
if links:
    try:
        links_obj = json.loads(links)
        tid = links_obj.get('id')
    except:
        pass

type_name = 'TODO' if glyph_type == 'T' else 'GOAL'
print(f"\033[1;36m{type_name}\033[0m: {tid or f'#{db_id}'}")
print(f"  Status: {status or 'OPEN'}")
print(f"  Topic: {topic or '-'}")
print(f"  Importance: {importance or '-'}")
print(f"  Due: {due or '-'}")
print(f"  Source: {source or '-'}")
print(f"  Created: {ts}")
print(f"  Text: {text}")
PYEOF
}

cmd_update() {
    local task_id="$1"
    shift

    if [[ -z "$task_id" ]]; then
        echo "ERROR: task ID required" >&2
        exit 1
    fi

    # Parse arguments
    local new_status=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status) new_status="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    if [[ -z "$new_status" ]]; then
        echo "ERROR: --status is required" >&2
        exit 1
    fi

    # Validate status
    new_status="${new_status^^}"  # Uppercase
    if [[ ! "$new_status" =~ ^(OPEN|IN_PROGRESS|DONE|BLOCKED)$ ]]; then
        echo "ERROR: Invalid status. Must be OPEN, IN_PROGRESS, DONE, or BLOCKED" >&2
        exit 1
    fi

    $PYTHON_BIN - "$DB_FILE" "$task_id" "$new_status" <<'PYEOF'
import sys
import sqlite3

db_path = sys.argv[1]
task_id = sys.argv[2]
new_status = sys.argv[3]

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Find the task
cursor.execute("""
SELECT id FROM chunks
WHERE anchor_type IN ('T', 'G')
  AND (links LIKE ? OR id = ?)
ORDER BY timestamp DESC
LIMIT 1
""", (f'%"id":"{task_id}"%', task_id if task_id.isdigit() else -1))

row = cursor.fetchone()
if not row:
    print(f"Task not found: {task_id}")
    sys.exit(1)

db_id = row[0]

# Update status
cursor.execute("UPDATE chunks SET anchor_choice = ? WHERE id = ?", (new_status, db_id))
conn.commit()
conn.close()

print(f"Updated {task_id} -> {new_status}")
PYEOF
}

cmd_done() {
    cmd_update "$1" --status DONE
}

cmd_block() {
    cmd_update "$1" --status BLOCKED
}

show_help() {
    head -40 "$0" | tail -38
}

main() {
    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        add) cmd_add "$@" ;;
        goal) cmd_add goal "$@" ;;
        list) cmd_list "$@" ;;
        get) cmd_get "$@" ;;
        update) cmd_update "$@" ;;
        done) cmd_done "$@" ;;
        block) cmd_block "$@" ;;
        help|--help|-h) show_help ;;
        *) echo "Unknown command: $cmd" >&2; show_help; exit 1 ;;
    esac
}

main "$@"
