#!/usr/bin/env bash
#
# mem-lessons.sh - Query lessons for a topic/project
#
# Usage:
#   ./mem-lessons.sh --topic <topic> [--limit <n>]
#   ./mem-lessons.sh -t <topic> [-l <n>]
#   ./mem-lessons.sh                     # All lessons
#
# Description:
#   Queries LESSON entries (type=L) for a given topic/project.
#   Outputs them in a compact format suitable for adding to LLM prompts
#   as a "you must follow these lessons" section.
#
# Output format (bullet list):
#   - [topic][ts] Lesson text here...
#   - [topic][ts] Another lesson...
#
# Examples:
#   ./mem-lessons.sh --topic VV
#   ./mem-lessons.sh --topic VV --limit 10
#   ./mem-lessons.sh -t memory -l 5

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
    head -22 "$0" | tail -20
}

main() {
    local topic=""
    local limit="10"
    local format="bullet"  # bullet or glyph

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --topic|-t) topic="$2"; shift 2 ;;
            --limit|-l) limit="$2"; shift 2 ;;
            --glyph) format="glyph"; shift ;;
            --help|-h) show_help; exit 0 ;;
            *) echo "Unknown option: $1" >&2; show_help; exit 1 ;;
        esac
    done

    $PYTHON_BIN - "$DB_FILE" "$topic" "$limit" "$format" <<'PYEOF'
import sys
import sqlite3
from datetime import datetime, timezone

db_path = sys.argv[1]
topic_filter = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
output_format = sys.argv[4] if len(sys.argv) > 4 else "bullet"

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

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Build query for LESSONs
where_clauses = ["anchor_type = 'L'"]
params = {}

if topic_filter:
    where_clauses.append("anchor_topic = :topic")
    params['topic'] = topic_filter

query = f"""
SELECT anchor_topic, text, timestamp, task_id
FROM chunks
WHERE {' AND '.join(where_clauses)}
ORDER BY timestamp DESC
LIMIT {limit}
"""

cursor.execute(query, params)
rows = cursor.fetchall()
conn.close()

if not rows:
    if topic_filter:
        print(f"# No lessons found for topic: {topic_filter}", file=sys.stderr)
    else:
        print("# No lessons found", file=sys.stderr)
    sys.exit(0)

# Output lessons
for topic, text, ts, task_id in rows:
    topic_str = topic or "general"
    ts_rel = format_relative_time(ts)
    content = (text or "").replace('\n', ' ').strip()

    if output_format == "glyph":
        # Glyph format: [LESSON][topic=X][ts=Y] text
        parts = ["[LESSON]", f"[topic={topic_str}]", f"[ts={ts_rel}]"]
        if task_id:
            parts.append(f"[task={task_id}]")
        print("".join(parts) + " " + content)
    else:
        # Bullet format for "lessons learned" section
        meta = f"[{topic_str}][{ts_rel}]"
        if task_id:
            meta += f"[task={task_id}]"
        print(f"- {meta} {content}")
PYEOF
}

main "$@"
