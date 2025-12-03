#!/usr/bin/env bash
#
# mem-task-context.sh - Generate compact context bundle for a task
#
# Usage:
#   ./mem-task-context.sh --task <id> [--limit <n>]
#   ./mem-task-context.sh -t <id> [-l <n>]
#
# Description:
#   Looks up a TODO by its ID, then gathers related memory entries:
#   - The TODO itself
#   - Recent FACTs, NOTEs, DECISIONs with the same topic
#   - ATTEMPTs, RESULTs, LESSONs, PHASEs linked to that task_id
#
#   Output is a compact, LLM-friendly text block (one line per entry).
#
# Examples:
#   ./mem-task-context.sh --task vv-001
#   ./mem-task-context.sh --task vv-001 --limit 20
#   ./mem-task-context.sh -t vv-001 -l 10

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

show_help() {
    head -20 "$0" | tail -18
}

main() {
    local task_id=""
    local limit="20"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --task|-t) task_id="$2"; shift 2 ;;
            --limit|-l) limit="$2"; shift 2 ;;
            --help|-h) show_help; exit 0 ;;
            *) echo "Unknown option: $1" >&2; show_help; exit 1 ;;
        esac
    done

    if [[ -z "$task_id" ]]; then
        echo "ERROR: --task <id> is required" >&2
        show_help
        exit 1
    fi

    $PYTHON_BIN - "$DB_FILE" "$task_id" "$limit" <<'PYEOF'
import sys
import sqlite3
import json
from datetime import datetime, timezone

db_path = sys.argv[1]
task_id = sys.argv[2]
limit = int(sys.argv[3])

def format_relative_time(ts_str):
    """Convert ISO timestamp to relative time string."""
    if not ts_str:
        return "?"
    ts_str = ts_str.replace('Z', '+00:00')
    try:
        ts = datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - ts
    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        return ts_str[:10]
    if total_seconds < 60:
        return f"{int(total_seconds)}s ago"
    elif total_seconds < 3600:
        return f"{int(total_seconds / 60)}m ago"
    elif total_seconds < 86400:
        return f"{int(total_seconds / 3600)}h ago"
    elif total_seconds < 2592000:
        return f"{int(total_seconds / 86400)}d ago"
    else:
        return ts_str[:10]

def type_label(t):
    """Convert type code to display label."""
    return {
        'd': 'DECISION', 'q': 'QUESTION', 'a': 'ACTION', 'f': 'FACT', 'n': 'NOTE', 'c': 'CONV',
        'T': 'TODO', 'G': 'GOAL', 'M': 'ATTEMPT', 'R': 'RESULT', 'L': 'LESSON', 'P': 'PHASE'
    }.get(t, t or '?')

def format_entry(row, is_todo=False):
    """Format a single entry as a compact glyph line."""
    anchor_type, topic, text, choice, ts, task_link, metric, links = row

    label = type_label(anchor_type)
    topic_str = topic or "general"
    ts_rel = format_relative_time(ts)

    # Build header parts
    parts = [f"[{label}]", f"[topic={topic_str}]", f"[ts={ts_rel}]"]

    # Add task-specific metadata
    if is_todo and task_id:
        parts.insert(1, f"[id={task_id}]")
    if anchor_type == 'T' and choice:
        parts.append(f"[status={choice}]")
    elif anchor_type == 'R' and choice:
        parts.append(f"[success={'true' if choice == 'success' else 'false'}]")
    elif anchor_type == 'd' and choice:
        parts.append(f"[choice={choice}]")
    if task_link and anchor_type in ['M', 'R', 'L', 'P']:
        parts.append(f"[task={task_link}]")
    if metric:
        parts.append(f"[metric={metric}]")

    # Special handling for PHASE entries: parse links JSON for from/to/round/error
    if anchor_type == 'P' and links:
        try:
            link_data = json.loads(links)
            from_phase = link_data.get('from', '?')
            to_phase = link_data.get('to', '?')
            round_num = link_data.get('round', '?')
            error_sig = link_data.get('error', 'none')
            parts.append(f"[from={from_phase}]")
            parts.append(f"[to={to_phase}]")
            parts.append(f"[round={round_num}]")
            parts.append(f"[error={error_sig}]")
        except (json.JSONDecodeError, TypeError):
            pass

    # Clean text (single line)
    content = (text or "").replace('\n', ' ').strip()

    return "".join(parts) + " " + content

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Step 1: Find the TODO by task_id (stored in links JSON)
cursor.execute("""
SELECT id, anchor_type, anchor_topic, text, anchor_choice, timestamp, links
FROM chunks
WHERE anchor_type IN ('T', 'G')
  AND links LIKE ?
ORDER BY timestamp DESC
LIMIT 1
""", (f'%"id":"{task_id}"%',))

todo_row = cursor.fetchone()

if not todo_row:
    print(f"# Task not found: {task_id}", file=sys.stderr)
    sys.exit(1)

db_id, todo_type, topic, text, status, ts, links = todo_row

# Output the TODO itself first
todo_entry = (todo_type, topic, text, status, ts, None, None, None)
print(format_entry(todo_entry, is_todo=True))

# Step 2: Find related entries
# - Same topic: recent FACTs, NOTEs, DECISIONs, ACTIONs
# - task_id link: ATTEMPTs, RESULTs, LESSONs, PHASEs

results = []

# Query by topic (excluding the TODO itself)
if topic:
    cursor.execute("""
    SELECT anchor_type, anchor_topic, text, anchor_choice, timestamp, task_id, metric, links
    FROM chunks
    WHERE anchor_topic = ?
      AND anchor_type IN ('d', 'f', 'n', 'a')
      AND id != ?
    ORDER BY timestamp DESC
    LIMIT ?
    """, (topic, db_id, limit))
    results.extend(cursor.fetchall())

# Query by task_id (ATTEMPTs, RESULTs, LESSONs, PHASEs)
cursor.execute("""
SELECT anchor_type, anchor_topic, text, anchor_choice, timestamp, task_id, metric, links
FROM chunks
WHERE task_id = ?
  AND anchor_type IN ('M', 'R', 'L', 'P')
ORDER BY timestamp DESC
LIMIT ?
""", (task_id, limit))
results.extend(cursor.fetchall())

conn.close()

# Sort all results by timestamp (newest first) and dedupe
seen_texts = {text}  # Already have TODO text
unique_results = []
for r in results:
    r_text = r[2]
    if r_text not in seen_texts:
        seen_texts.add(r_text)
        unique_results.append(r)

# Sort by timestamp (most recent first)
def parse_ts(ts_str):
    if not ts_str:
        return datetime.min.replace(tzinfo=timezone.utc)
    ts_str = ts_str.replace('Z', '+00:00')
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except:
        return datetime.min.replace(tzinfo=timezone.utc)

unique_results.sort(key=lambda r: parse_ts(r[4]), reverse=True)

# Limit total output
unique_results = unique_results[:limit - 1]  # -1 for the TODO

# Output related entries
for r in unique_results:
    print(format_entry(r))

# If no related entries found
if not unique_results:
    print("# No related entries found", file=sys.stderr)
PYEOF
}

main "$@"
