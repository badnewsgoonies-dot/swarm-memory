#!/usr/bin/env bash
#
# affordance-log.sh - Show recent affordance-reasoning RESULT glyphs
#
# Usage:
#   ./affordance-log.sh             # last 10 episodes
#   ./affordance-log.sh 5           # last 5 episodes
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_FILE="${MEMORY_DB:-$SCRIPT_DIR/memory.db}"

LIMIT="${1:-10}"

if [[ ! -f "$DB_FILE" ]]; then
    echo "[affordance-log] No database at $DB_FILE" >&2
    exit 1
fi

python3 - "$DB_FILE" "$LIMIT" <<'PYEOF'
import sys
import sqlite3
from textwrap import indent

db_path = sys.argv[1]
limit = int(sys.argv[2])

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute(
    """
    SELECT timestamp, text
    FROM chunks
    WHERE anchor_type = 'R'
      AND anchor_topic = 'affordance-reasoning'
      AND text IS NOT NULL
    ORDER BY timestamp DESC
    LIMIT ?
    """,
    (limit,),
)
rows = cursor.fetchall()
conn.close()

if not rows:
    print("[affordance-log] No affordance-reasoning RESULT glyphs found.")
    sys.exit(0)

for ts, text in rows:
    ts_disp = ts or "?"
    print("=" * 80)
    print(f"[{ts_disp}]")
    print(indent(text.strip(), "  "))
    print()
PYEOF

