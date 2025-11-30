#!/usr/bin/env bash
#
# mem-db.sh - SQLite memory database management
#
# Usage:
#   ./mem-db.sh init              # Create database and schema
#   ./mem-db.sh migrate           # Add multi-chat columns to existing database
#   ./mem-db.sh sync              # Sync from anchors.jsonl
#   ./mem-db.sh sync --dry-run    # Preview sync without writing
#   ./mem-db.sh status            # Show database statistics and sync state
#   ./mem-db.sh query [filters]   # Search database with filters
#   ./mem-db.sh write [params]    # Insert new memory entry with scope metadata
#
# Query filters (same syntax as mem-search.sh):
#   t=d              # type = decision (d/q/a/f/n)
#   topic=memory     # exact topic match
#   text=keyword     # text contains keyword (case-insensitive)
#   session=name     # exact session match
#   source=claude    # exact source match
#   choice=value     # exact choice match
#   status=value     # exact status match (same as choice)
#   since=2025-11-25 # entries after date
#   until=2025-11-30 # entries before date
#   scope=shared     # scope filter (shared/chat/agent/team)
#   chat_id=abc123   # exact chat ID match
#   role=architect   # exact agent role match
#   visibility=public # visibility filter (public/private/internal)
#   project=myproj   # exact project ID match
#   limit=10         # max results (default: 20)
#   --json           # output as JSONL arrays
#
# Examples:
#   ./mem-db.sh query t=d                    # All decisions
#   ./mem-db.sh query topic=memory           # All with topic "memory"
#   ./mem-db.sh query text=embedding         # Text search
#   ./mem-db.sh query t=d topic=memory --json
#   ./mem-db.sh query limit=5
#   ./mem-db.sh query scope=shared           # Shared scope only
#   ./mem-db.sh query scope=chat chat_id=abc123  # Chat-specific
#   ./mem-db.sh query role=architect         # Agent role filter
#   ./mem-db.sh query visibility=public project=myproj  # Combined filters
#
# Write parameters:
#   Required:
#     t=<type>          # d/q/a/f/n (decision/question/action/fact/note)
#     text=<content>    # The main text content
#   Optional:
#     topic=<topic>     # Category/topic
#     choice=<choice>   # For decisions
#     rationale=<rationale>  # Why this choice
#     scope=<scope>     # shared/chat/agent/team (default: shared)
#     chat_id=<id>      # Chat identifier
#     role=<role>       # Agent role
#     visibility=<vis>  # public/private/internal (default: public)
#     project=<id>      # Project identifier
#     session=<session> # Session name
#     source=<source>   # Source identifier
#
# Write examples:
#   ./mem-db.sh write t=d topic=auth text="Use JWT" scope=shared visibility=public
#   ./mem-db.sh write t=n topic=scratch text="thinking..." scope=chat chat_id=abc123
#   ./mem-db.sh write t=f text="Found a bug" role=coder project=myproj
#

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

cmd_init() {
    if [[ -f "$DB_FILE" ]]; then
        echo "Database already exists: $DB_FILE" >&2
        echo "Remove it first if you want to reinitialize." >&2
        exit 1
    fi

    echo "Creating database: $DB_FILE"

    $PYTHON_BIN <<PYEOF
import sqlite3

conn = sqlite3.connect("$DB_FILE")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    bucket TEXT DEFAULT 'anchor',
    timestamp TEXT,
    text TEXT NOT NULL,
    anchor_type TEXT,
    anchor_topic TEXT,
    anchor_choice TEXT,
    anchor_rationale TEXT,
    anchor_session TEXT,
    anchor_source TEXT,
    source_line INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    -- Multi-chat fields (Phase 3)
    scope TEXT DEFAULT 'shared',
    chat_id TEXT,
    agent_role TEXT,
    visibility TEXT DEFAULT 'public',
    project_id TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS sync_state (
    source_file TEXT PRIMARY KEY,
    last_line INTEGER,
    last_sync TEXT
)
""")

cursor.execute("CREATE INDEX IF NOT EXISTS idx_type ON chunks(anchor_type)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_topic ON chunks(anchor_topic)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON chunks(timestamp)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_scope ON chunks(scope)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON chunks(chat_id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_visibility ON chunks(visibility)")

conn.commit()
conn.close()
PYEOF

    echo "Database initialized successfully."
    echo "Schema created with tables: chunks, sync_state"
    echo "Indexes created: idx_type, idx_topic, idx_timestamp, idx_scope, idx_chat_id, idx_visibility"
}

cmd_migrate() {
    if [[ ! -f "$DB_FILE" ]]; then
        echo "Database not found: $DB_FILE" >&2
        echo "Run './mem-db.sh init' first." >&2
        exit 1
    fi

    echo "Migrating database: $DB_FILE"

    $PYTHON_BIN <<PYEOF
import sqlite3

conn = sqlite3.connect("$DB_FILE")
cursor = conn.cursor()

# Get existing columns
cursor.execute("PRAGMA table_info(chunks)")
existing_columns = {row[1] for row in cursor.fetchall()}

# Add columns if they don't exist
columns_to_add = [
    ("scope", "TEXT DEFAULT 'shared'"),
    ("chat_id", "TEXT"),
    ("agent_role", "TEXT"),
    ("visibility", "TEXT DEFAULT 'public'"),
    ("project_id", "TEXT")
]

added_columns = []
for col_name, col_def in columns_to_add:
    if col_name not in existing_columns:
        cursor.execute(f"ALTER TABLE chunks ADD COLUMN {col_name} {col_def}")
        added_columns.append(col_name)
        print(f"Added column: {col_name}")
    else:
        print(f"Column already exists: {col_name}")

# Create indexes if they don't exist
cursor.execute("CREATE INDEX IF NOT EXISTS idx_scope ON chunks(scope)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON chunks(chat_id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_visibility ON chunks(visibility)")

if added_columns:
    print(f"Created indexes: idx_scope, idx_chat_id, idx_visibility")
else:
    print("All indexes already exist")

conn.commit()
conn.close()
PYEOF

    echo "Migration completed successfully."
}

cmd_sync() {
    local dry_run=""
    for arg in "$@"; do
        [[ "$arg" == "--dry-run" ]] && dry_run="--dry-run"
    done

    local sync_script="$SCRIPT_DIR/mem-sync.py"
    if [[ ! -x "$sync_script" ]]; then
        echo "ERROR: mem-sync.py not found or not executable" >&2
        exit 1
    fi

    $PYTHON_BIN "$sync_script" --db "$DB_FILE" $dry_run
}

cmd_status() {
    if [[ ! -f "$DB_FILE" ]]; then
        echo "Database not found: $DB_FILE" >&2
        echo "Run './mem-db.sh init' first." >&2
        exit 1
    fi

    $PYTHON_BIN <<PYEOF
import sqlite3
import os
from datetime import datetime

db_file = "$DB_FILE"
conn = sqlite3.connect(db_file)
cursor = conn.cursor()

# Get database file size
db_size = os.path.getsize(db_file)
if db_size < 1024:
    size_str = f"{db_size} B"
elif db_size < 1024 * 1024:
    size_str = f"{db_size / 1024:.0f} KB"
else:
    size_str = f"{db_size / (1024 * 1024):.1f} MB"

# Get total chunk count
cursor.execute("SELECT COUNT(*) FROM chunks")
total_chunks = cursor.fetchone()[0]

# Get chunk counts by type
cursor.execute("""
    SELECT anchor_type, COUNT(*)
    FROM chunks
    WHERE anchor_type IS NOT NULL
    GROUP BY anchor_type
    ORDER BY anchor_type
""")
type_counts = cursor.fetchall()

# Get sync state
cursor.execute("""
    SELECT source_file, last_line, last_sync
    FROM sync_state
    ORDER BY source_file
""")
sync_states = cursor.fetchall()

conn.close()

# Print status
print("=== Memory Database Status ===")
print(f"Database: {db_file}")
print(f"Size: {size_str}")
print()
print(f"Chunks: {total_chunks} total")

# Type mapping
type_map = {
    'd': 'decisions',
    'q': 'questions',
    'a': 'actions',
    'f': 'facts',
    'n': 'notes'
}

if type_counts:
    for anchor_type, count in type_counts:
        type_name = type_map.get(anchor_type, anchor_type)
        print(f"  - {type_name} ({anchor_type}): {count}")
else:
    print("  - No chunks with types")

print()
print("Sync State:")
if sync_states:
    for source_file, last_line, last_sync in sync_states:
        print(f"  - {source_file}: line {last_line} (synced {last_sync})")
else:
    print("  - No sync state recorded")

print()
print(f"Embeddings: 0/{total_chunks} (0%) - not yet implemented")
PYEOF
}

cmd_query() {
    if [[ ! -f "$DB_FILE" ]]; then
        echo "Database not found: $DB_FILE" >&2
        echo "Run './mem-db.sh init' first." >&2
        exit 1
    fi

    # Collect args for Python
    local args=""
    local json_output=0
    for arg in "$@"; do
        if [[ "$arg" == "--json" ]]; then
            json_output=1
        else
            args="$args $arg"
        fi
    done

    $PYTHON_BIN - "$DB_FILE" "$json_output" $args <<'PYEOF'
import sys
import sqlite3
import json

db_path = sys.argv[1]
json_output = sys.argv[2] == "1"
filters = sys.argv[3:]

# Type abbreviation expansion
def expand_type(t):
    type_map = {
        'd': 'd', 'decision': 'd',
        'q': 'q', 'question': 'q',
        'a': 'a', 'action': 'a',
        'f': 'f', 'fact': 'f',
        'n': 'n', 'note': 'n'
    }
    return type_map.get(t.lower(), t)

# Parse filters
where_clauses = []
params = {}
limit = 20

for f in filters:
    if '=' not in f:
        continue
    key, val = f.split('=', 1)

    if key == 't' or key == 'type':
        params['type'] = expand_type(val)
        where_clauses.append("anchor_type = :type")
    elif key == 'topic':
        params['topic'] = val
        where_clauses.append("anchor_topic = :topic")
    elif key == 'text':
        params['text'] = f"%{val}%"
        where_clauses.append("text LIKE :text")
    elif key == 'session':
        params['session'] = val
        where_clauses.append("anchor_session = :session")
    elif key == 'source':
        params['source'] = val
        where_clauses.append("anchor_source = :source")
    elif key == 'choice':
        params['choice'] = val
        where_clauses.append("anchor_choice = :choice")
    elif key == 'status':
        params['status'] = val
        where_clauses.append("anchor_choice = :status")
    elif key == 'since':
        params['since'] = f"{val}T00:00:00Z"
        where_clauses.append("timestamp >= :since")
    elif key == 'until':
        params['until'] = f"{val}T23:59:59Z"
        where_clauses.append("timestamp <= :until")
    elif key == 'scope':
        params['scope'] = val
        where_clauses.append("scope = :scope")
    elif key == 'chat_id':
        params['chat_id'] = val
        where_clauses.append("chat_id = :chat_id")
    elif key == 'role':
        params['role'] = val
        where_clauses.append("agent_role = :role")
    elif key == 'visibility':
        params['visibility'] = val
        where_clauses.append("visibility = :visibility")
    elif key == 'project':
        params['project'] = val
        where_clauses.append("project_id = :project")
    elif key == 'limit':
        limit = int(val)

# Build query
query = """
SELECT
    anchor_type,
    anchor_topic,
    text,
    anchor_choice,
    anchor_rationale,
    timestamp,
    anchor_session,
    anchor_source,
    scope,
    chat_id,
    agent_role,
    visibility,
    project_id
FROM chunks
"""

if where_clauses:
    query += " WHERE " + " AND ".join(where_clauses)

query += f" ORDER BY timestamp DESC LIMIT {limit}"

# Execute query
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute(query, params)
results = cursor.fetchall()
conn.close()

if not results:
    print("No matches found.", file=sys.stderr)
    sys.exit(0)

# Output results
if json_output:
    for row in results:
        print(json.dumps(list(row)))
else:
    # Type label mapping
    type_labels = {
        'd': 'DECISION',
        'q': 'QUESTION',
        'a': 'ACTION',
        'f': 'FACT',
        'n': 'NOTE'
    }

    for row in results:
        anchor_type, topic, text, choice, rationale, ts, session, source, scope, chat_id, agent_role, visibility, project_id = row

        type_label = type_labels.get(anchor_type, anchor_type or '?')
        topic = topic or '?'
        text = text or ''
        choice = choice or ''
        ts = ts or '?'
        session = session or '?'

        # ANSI color codes
        print(f"\033[1;36m[{type_label}]\033[0m \033[1;33m{topic}\033[0m")
        print(f"  {text}")
        if choice:
            print(f"  \033[32mChoice:\033[0m {choice}")
        ts_short = ts[:10] if len(ts) >= 10 else ts
        meta_parts = [ts_short, session]
        if scope and scope != 'shared':
            meta_parts.append(f"scope={scope}")
        if chat_id:
            meta_parts.append(f"chat={chat_id[:8]}...")
        if agent_role:
            meta_parts.append(f"role={agent_role}")
        if visibility and visibility != 'public':
            meta_parts.append(f"vis={visibility}")
        print(f"  \033[90m{' | '.join(meta_parts)}\033[0m")
        print()
PYEOF
}

cmd_write() {
    if [[ ! -f "$DB_FILE" ]]; then
        echo "Database not found: $DB_FILE" >&2
        echo "Run './mem-db.sh init' first." >&2
        exit 1
    fi

    $PYTHON_BIN - "$DB_FILE" "$SCRIPT_DIR" "$@" <<'PYEOF'
import sys
import sqlite3
import json
from datetime import datetime
import os

db_path = sys.argv[1]
script_dir = sys.argv[2]
params_raw = sys.argv[3:]

# Type abbreviation expansion
def expand_type(t):
    type_map = {
        'd': 'd', 'decision': 'd',
        'q': 'q', 'question': 'q',
        'a': 'a', 'action': 'a',
        'f': 'f', 'fact': 'f',
        'n': 'n', 'note': 'n'
    }
    return type_map.get(t.lower(), t)

# Parse parameters
params = {}
for p in params_raw:
    if '=' not in p:
        continue
    key, val = p.split('=', 1)
    params[key] = val

# Required fields
if 't' not in params and 'type' not in params:
    print("ERROR: Required parameter 't' or 'type' not provided", file=sys.stderr)
    print("Usage: ./mem-db.sh write t=<type> text=<content> [options]", file=sys.stderr)
    sys.exit(1)

if 'text' not in params:
    print("ERROR: Required parameter 'text' not provided", file=sys.stderr)
    print("Usage: ./mem-db.sh write t=<type> text=<content> [options]", file=sys.stderr)
    sys.exit(1)

# Extract and validate type
entry_type = expand_type(params.get('t') or params.get('type'))
if entry_type not in ['d', 'q', 'a', 'f', 'n']:
    print(f"ERROR: Invalid type '{entry_type}'. Must be d/q/a/f/n", file=sys.stderr)
    sys.exit(1)

# Generate timestamp
try:
    from datetime import UTC
    timestamp = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
except ImportError:
    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

# Build entry data
entry = {
    'bucket': 'anchor',
    'timestamp': timestamp,
    'text': params.get('text'),
    'anchor_type': entry_type,
    'anchor_topic': params.get('topic'),
    'anchor_choice': params.get('choice'),
    'anchor_rationale': params.get('rationale'),
    'anchor_session': params.get('session'),
    'anchor_source': params.get('source'),
    'scope': params.get('scope', 'shared'),
    'chat_id': params.get('chat_id'),
    'agent_role': params.get('role'),
    'visibility': params.get('visibility', 'public'),
    'project_id': params.get('project'),
    'source_line': None
}

# Insert into database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("""
    INSERT INTO chunks (
        bucket, timestamp, text, anchor_type, anchor_topic,
        anchor_choice, anchor_rationale, anchor_session, anchor_source,
        scope, chat_id, agent_role, visibility, project_id, source_line
    ) VALUES (
        :bucket, :timestamp, :text, :anchor_type, :anchor_topic,
        :anchor_choice, :anchor_rationale, :anchor_session, :anchor_source,
        :scope, :chat_id, :agent_role, :visibility, :project_id, :source_line
    )
""", entry)

entry_id = cursor.lastrowid
conn.commit()
conn.close()

# Append to anchors.jsonl and update sync_state
# Format: [type, topic, text, choice, rationale, timestamp, session, source]
anchors_file = os.path.join(script_dir, "anchors.jsonl")
jsonl_entry = [
    entry['anchor_type'],
    entry['anchor_topic'],
    entry['text'],
    entry['anchor_choice'],
    entry['anchor_rationale'],
    entry['timestamp'],
    entry['anchor_session'],
    entry['anchor_source']
]

try:
    with open(anchors_file, 'a') as f:
        f.write(json.dumps(jsonl_entry) + '\n')

    # Update sync_state to prevent duplicate import on next sync
    line_count = sum(1 for _ in open(anchors_file))
    conn2 = sqlite3.connect(db_path)
    try:
        from datetime import UTC
        now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    except ImportError:
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    conn2.execute("""
        INSERT INTO sync_state (source_file, last_line, last_sync)
        VALUES ('anchors.jsonl', ?, ?)
        ON CONFLICT(source_file) DO UPDATE SET
            last_line = excluded.last_line,
            last_sync = excluded.last_sync
    """, (line_count, now))
    conn2.commit()
    conn2.close()
except Exception as e:
    print(f"WARNING: Failed to append to anchors.jsonl: {e}", file=sys.stderr)

# Output result
entry['id'] = entry_id
print(json.dumps(entry, indent=2))
PYEOF
}

main() {
    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        init) cmd_init "$@" ;;
        migrate) cmd_migrate "$@" ;;
        sync) cmd_sync "$@" ;;
        status) cmd_status "$@" ;;
        query) cmd_query "$@" ;;
        write) cmd_write "$@" ;;
        *) echo "Usage: $0 {init|migrate|sync|status|query|write}" >&2; exit 1 ;;
    esac
}

main "$@"
