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
#   ./mem-db.sh embed             # Generate embeddings for chunks
#   ./mem-db.sh embed --dry-run   # Preview without generating
#   ./mem-db.sh embed --force     # Re-embed even if already embedded
#   ./mem-db.sh semantic "query"  # Semantic search with hybrid scoring
#   ./mem-db.sh semantic "query" --limit 10 --tau 7 --beta 0.3
#   ./mem-db.sh render [filters]  # Render entries in compact glyph format for LLM
#   ./mem-db.sh consolidate --recent  # Consolidate most recent entry
#   ./mem-db.sh consolidate --id 123  # Consolidate specific entry
#   ./mem-db.sh consolidate --all     # Consolidate all entries
#   ./mem-db.sh prune 30              # Delete deprecated entries older than 30 days
#   ./mem-db.sh prune --dry-run       # Preview what would be pruned
#   ./mem-db.sh health                # Show health dashboard with diagnostics
#
# Query filters (same syntax as mem-search.sh):
#   t=d              # type = decision (d/q/a/f/n/c or T/G/M/R/L for task types)
#   topic=memory     # exact topic match
#   task_id=vv-001   # filter by linked task ID (for attempts/results/lessons)
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
#     t=<type>          # Core: d/q/a/f/n/c | Task: T/G/M/R/L (todo/goal/attempt/result/lesson)
#     text=<content>    # The main text content
#   Optional:
#     topic=<topic>     # Category/topic (use as project tag for tasks)
#     choice=<choice>   # For decisions, TODO status (OPEN/IN_PROGRESS/DONE/BLOCKED), or RESULT success
#     rationale=<rationale>  # Why this choice
#     importance=<h|m|l> # Importance flag for prioritization (H/M/L)
#     due=<ISO date>     # Due date or deadline hint
#     links=<json/url>   # Related URLs or references
#     task_id=<id>       # Links ATTEMPT/RESULT/LESSON to a TODO/GOAL id
#     metric=<string>    # Numeric metric for RESULT (e.g., "tests_passed=12/12")
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
    importance TEXT,
    due TEXT,
    links TEXT,
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
cursor.execute("CREATE INDEX IF NOT EXISTS idx_due ON chunks(due)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_due ON chunks(due)")

# Pending changes and audit log (governor)
cursor.execute("""
CREATE TABLE IF NOT EXISTS pending_changes (
    id INTEGER PRIMARY KEY,
    action_type TEXT NOT NULL,
    action_data TEXT NOT NULL,
    proposed_by TEXT,
    proposed_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT (datetime('now')),
    action_type TEXT NOT NULL,
    action_data TEXT,
    decision TEXT NOT NULL,
    reason TEXT,
    actor TEXT
)
""")

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
    ("project_id", "TEXT"),
    ("importance", "TEXT"),
    ("due", "TEXT"),
    ("links", "TEXT"),
    ("embedding", "BLOB"),
    ("embedding_model", "TEXT"),
    ("embedding_dim", "INTEGER"),
    ("status", "TEXT DEFAULT 'active'"),
    ("superseded_by", "INTEGER"),
    ("superseded_at", "TEXT"),
    # Task-centric glyph fields (Phase 1)
    ("task_id", "TEXT"),       # Links ATTEMPT/RESULT/LESSON to a TODO/GOAL id
    ("metric", "TEXT")         # Numeric metric for RESULT (e.g., "tests_passed=12/12")
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
cursor.execute("CREATE INDEX IF NOT EXISTS idx_due ON chunks(due)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_id ON chunks(task_id)")

# Create topic_index table for hierarchical retrieval
cursor.execute("""
CREATE TABLE IF NOT EXISTS topic_index (
    topic TEXT PRIMARY KEY,
    embedding BLOB,
    embedding_model TEXT,
    embedding_dim INTEGER,
    chunk_count INTEGER DEFAULT 0,
    updated_at TEXT
)
""")
print("Created topic_index table (if not exists)")

# Create pending_changes table for governor escalation
cursor.execute("""
CREATE TABLE IF NOT EXISTS pending_changes (
    id INTEGER PRIMARY KEY,
    action_type TEXT NOT NULL,
    action_data TEXT NOT NULL,
    proposed_by TEXT,
    proposed_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT
)
""")
print("Created pending_changes table (if not exists)")

# Create audit_log table for all governor decisions
cursor.execute("""
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT (datetime('now')),
    action_type TEXT NOT NULL,
    action_data TEXT,
    decision TEXT NOT NULL,
    reason TEXT,
    actor TEXT
)
""")
print("Created audit_log table (if not exists)")

if added_columns:
    print(f"Created indexes: idx_scope, idx_chat_id, idx_visibility, idx_due")
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
    'n': 'notes',
    'c': 'conversations',
    # Task-centric types
    'T': 'todos',
    'G': 'goals',
    'M': 'attempts',
    'R': 'results',
    'L': 'lessons'
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

# Check if embedding column exists
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(chunks)")
columns = {row[1] for row in cursor.fetchall()}

print()
if 'embedding' in columns:
    cursor.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
    embedded_count = cursor.fetchone()[0]
    pct = 100 * embedded_count // total_chunks if total_chunks > 0 else 0
    print(f"Embeddings: {embedded_count}/{total_chunks} ({pct}%)")

    # Show model breakdown if any embeddings exist
    if embedded_count > 0:
        cursor.execute("""
            SELECT embedding_model, embedding_dim, COUNT(*)
            FROM chunks
            WHERE embedding IS NOT NULL
            GROUP BY embedding_model, embedding_dim
        """)
        rows = cursor.fetchall()
        if rows:
            print("Embedding models:")
            for model, dim, count in rows:
                model = model or "unknown"
                dim = dim or 0
                print(f"  - {model} (dim={dim}): {count} chunks")
else:
    print(f"Embeddings: not configured (run ./mem-db.sh migrate)")

conn.close()
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

def format_relative_time(ts_str):
    """Convert ISO timestamp to relative time + freshness flag."""
    if not ts_str:
        return ("?", False)
    from datetime import datetime, timezone
    ts_str = ts_str.replace('Z', '+00:00')
    try:
        ts = datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        return ("?", False)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - ts
    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        return (ts_str[:10], False)
    is_fresh = total_seconds < 3600  # < 1 hour
    if total_seconds < 60:
        return (f"{int(total_seconds)}s ago", is_fresh)
    elif total_seconds < 3600:
        return (f"{int(total_seconds / 60)}m ago", is_fresh)
    elif total_seconds < 86400:
        return (f"{int(total_seconds / 3600)}h ago", is_fresh)
    elif total_seconds < 2592000:
        return (f"{int(total_seconds / 86400)}d ago", is_fresh)
    else:
        return (ts_str[:10], False)

# Type abbreviation expansion
# Core types: d/q/a/f/n/c (decision/question/action/fact/note/conversation)
# Task types: T/G/M/R/L (todo/goal/attempt/result/lesson) - uppercase to distinguish
def expand_type(t):
    type_map = {
        'd': 'd', 'decision': 'd',
        'q': 'q', 'question': 'q',
        'a': 'a', 'action': 'a',
        'f': 'f', 'fact': 'f',
        'n': 'n', 'note': 'n',
        'c': 'c', 'conversation': 'c',
        # Task-centric types (uppercase)
        't': 'T', 'todo': 'T',
        'g': 'G', 'goal': 'G',
        'm': 'M', 'attempt': 'M',
        'r': 'R', 'result': 'R',
        'l': 'L', 'lesson': 'L'
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
    elif key == 'recent':
        import re
        match = re.match(r'^(\d+)([hdwm])$', val.strip().lower())
        if not match:
            print(f"ERROR: Invalid recent format '{val}'. Use: 1h, 24h, 7d, 1w, 1m", file=sys.stderr)
            sys.exit(1)
        amount, unit = int(match.group(1)), match.group(2)
        from datetime import datetime, timedelta, timezone
        delta_map = {'h': timedelta(hours=amount), 'd': timedelta(days=amount), 'w': timedelta(weeks=amount), 'm': timedelta(days=amount*30)}
        cutoff = datetime.now(timezone.utc) - delta_map[unit]
        params['since'] = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')
        where_clauses.append("timestamp >= :since")
    elif key == 'task_id':
        params['task_id'] = val
        where_clauses.append("task_id = :task_id")
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
    project_id,
    importance,
    due,
    links,
    task_id,
    metric
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
        'n': 'NOTE',
        'c': 'CONVERSATION',
        # Task-centric types
        'T': 'TODO',
        'G': 'GOAL',
        'M': 'ATTEMPT',
        'R': 'RESULT',
        'L': 'LESSON'
    }

    for row in results:
        anchor_type, topic, text, choice, rationale, ts, session, source, scope, chat_id, agent_role, visibility, project_id, importance, due, links, task_id, metric = row

        type_label = type_labels.get(anchor_type, anchor_type or '?')
        topic = topic or '?'
        text = text or ''
        choice = choice or ''
        ts = ts or '?'
        session = session or '?'

        # ANSI color codes
        print(f"\033[1;36m[{type_label}]\033[0m \033[1;33m{topic}\033[0m")
        print(f"  {text}")
        # For task types, show status/success differently
        if anchor_type in ['T', 'G'] and choice:
            print(f"  \033[32mStatus:\033[0m {choice}")
        elif anchor_type == 'R' and choice:
            success_color = "\033[32m" if choice == 'success' else "\033[31m"
            print(f"  {success_color}Result:\033[0m {choice}")
            if metric:
                print(f"  \033[34mMetric:\033[0m {metric}")
        elif choice:
            print(f"  \033[32mChoice:\033[0m {choice}")
        # Show task_id link for ATTEMPT/RESULT/LESSON
        if task_id and anchor_type in ['M', 'R', 'L']:
            print(f"  \033[35mTask:\033[0m {task_id}")
        ts_rel, is_fresh = format_relative_time(ts)
        fresh_marker = " \033[1;92m[FRESH]\033[0m" if is_fresh else ""
        meta_parts = [ts_rel + fresh_marker, session]
        if scope and scope != 'shared':
            meta_parts.append(f"scope={scope}")
        if chat_id:
            meta_parts.append(f"chat={chat_id[:8]}...")
        if agent_role:
            meta_parts.append(f"role={agent_role}")
        if visibility and visibility != 'public':
            meta_parts.append(f"vis={visibility}")
        if importance:
            meta_parts.append(f"imp={importance}")
        if due:
            meta_parts.append(f"due={due[:10]}")
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
# Core types: d/q/a/f/n/c (decision/question/action/fact/note/conversation)
# Task types: T/G/M/R/L (todo/goal/attempt/result/lesson) - uppercase to distinguish
def expand_type(t):
    type_map = {
        'd': 'd', 'decision': 'd',
        'q': 'q', 'question': 'q',
        'a': 'a', 'action': 'a',
        'f': 'f', 'fact': 'f',
        'n': 'n', 'note': 'n',
        'c': 'c', 'conversation': 'c',
        # Task-centric types (uppercase)
        't': 'T', 'todo': 'T',
        'g': 'G', 'goal': 'G',
        'm': 'M', 'attempt': 'M',
        'r': 'R', 'result': 'R',
        'l': 'L', 'lesson': 'L'
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
valid_types = ['d', 'q', 'a', 'f', 'n', 'c', 'T', 'G', 'M', 'R', 'L']
if entry_type not in valid_types:
    print(f"ERROR: Invalid type '{entry_type}'. Must be d/q/a/f/n/c or T/G/M/R/L", file=sys.stderr)
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
    'importance': params.get('importance'),
    'due': params.get('due'),
    'links': params.get('links'),
    'scope': params.get('scope', 'shared'),
    'chat_id': params.get('chat_id'),
    'agent_role': params.get('role'),
    'visibility': params.get('visibility', 'public'),
    'project_id': params.get('project'),
    'source_line': None,
    # Task-centric fields
    'task_id': params.get('task_id'),
    'metric': params.get('metric')
}

# Insert into database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("""
    INSERT INTO chunks (
        bucket, timestamp, text, anchor_type, anchor_topic,
        anchor_choice, anchor_rationale, anchor_session, anchor_source,
        importance, due, links,
        scope, chat_id, agent_role, visibility, project_id, source_line,
        task_id, metric
    ) VALUES (
        :bucket, :timestamp, :text, :anchor_type, :anchor_topic,
        :anchor_choice, :anchor_rationale, :anchor_session, :anchor_source,
        :importance, :due, :links,
        :scope, :chat_id, :agent_role, :visibility, :project_id, :source_line,
        :task_id, :metric
    )
""", entry)

entry_id = cursor.lastrowid
conn.commit()
conn.close()

# Append to anchors.jsonl and update sync_state
# Format: [type, topic, text, choice, rationale, timestamp, session, source, importance, due, links, task_id, metric]
anchors_file = os.path.join(script_dir, "anchors.jsonl")
jsonl_entry = [
    entry['anchor_type'],
    entry['anchor_topic'],
    entry['text'],
    entry['anchor_choice'],
    entry['anchor_rationale'],
    entry['timestamp'],
    entry['anchor_session'],
    entry['anchor_source'],
    entry['importance'],
    entry['due'],
    entry['links'],
    entry['task_id'],
    entry['metric']
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

cmd_embed() {
    local embed_script="$SCRIPT_DIR/mem-embed.py"
    if [[ ! -x "$embed_script" ]]; then
        echo "ERROR: mem-embed.py not found or not executable" >&2
        exit 1
    fi

    # Pass all arguments through
    $PYTHON_BIN "$embed_script" --db "$DB_FILE" "$@"
}

cmd_semantic() {
    local semantic_script="$SCRIPT_DIR/mem-semantic.py"
    if [[ ! -x "$semantic_script" ]]; then
        echo "ERROR: mem-semantic.py not found or not executable" >&2
        exit 1
    fi

    # Pass all arguments through
    $PYTHON_BIN "$semantic_script" --db "$DB_FILE" "$@"
}

cmd_topic_index() {
    # Build/update topic index with aggregated embeddings
    if [[ ! -f "$DB_FILE" ]]; then
        echo "Database not found: $DB_FILE" >&2
        exit 1
    fi

    $PYTHON_BIN - "$DB_FILE" "$@" <<'PYEOF'
import sys
import sqlite3
import struct
from datetime import datetime

db_path = sys.argv[1]
dry_run = "--dry-run" in sys.argv

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get all topics with embeddings
cursor.execute("""
    SELECT anchor_topic, embedding, embedding_model, embedding_dim
    FROM chunks
    WHERE embedding IS NOT NULL AND anchor_topic IS NOT NULL
      AND (status IS NULL OR status = 'active')
""")
rows = cursor.fetchall()

if not rows:
    print("No embedded chunks with topics found.")
    sys.exit(0)

# Group embeddings by topic
topic_embeddings = {}
for topic, blob, model, dim in rows:
    if topic not in topic_embeddings:
        topic_embeddings[topic] = {'embeddings': [], 'model': model, 'dim': dim}
    emb = list(struct.unpack(f'{dim}f', blob))
    topic_embeddings[topic]['embeddings'].append(emb)

print(f"Found {len(topic_embeddings)} topics to index")

# Compute mean embedding for each topic
try:
    from datetime import UTC
    now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
except ImportError:
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

indexed = 0
for topic, data in topic_embeddings.items():
    embeddings = data['embeddings']
    dim = data['dim']
    model = data['model']
    count = len(embeddings)

    # Compute mean embedding
    mean_emb = [0.0] * dim
    for emb in embeddings:
        for i, v in enumerate(emb):
            mean_emb[i] += v
    mean_emb = [v / count for v in mean_emb]

    # Normalize
    norm = sum(v * v for v in mean_emb) ** 0.5
    if norm > 0:
        mean_emb = [v / norm for v in mean_emb]

    # Pack to blob
    blob = struct.pack(f'{dim}f', *mean_emb)

    if dry_run:
        print(f"  Would index: {topic} ({count} chunks)")
    else:
        cursor.execute("""
            INSERT INTO topic_index (topic, embedding, embedding_model, embedding_dim, chunk_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic) DO UPDATE SET
                embedding = excluded.embedding,
                embedding_model = excluded.embedding_model,
                embedding_dim = excluded.embedding_dim,
                chunk_count = excluded.chunk_count,
                updated_at = excluded.updated_at
        """, (topic, blob, model, dim, count, now))
        print(f"  Indexed: {topic} ({count} chunks)")
    indexed += 1

if not dry_run:
    conn.commit()
    print(f"\nIndexed {indexed} topics")
else:
    print(f"\n(dry-run: would index {indexed} topics)")

conn.close()
PYEOF
}

cmd_consolidate() {
    local consolidate_script="$SCRIPT_DIR/mem-consolidate.py"
    if [[ ! -x "$consolidate_script" ]]; then
        echo "ERROR: mem-consolidate.py not found or not executable" >&2
        exit 1
    fi

    # Pass all arguments through
    $PYTHON_BIN "$consolidate_script" --db "$DB_FILE" "$@"
}

cmd_prune() {
    # Prune deprecated entries older than N days
    if [[ ! -f "$DB_FILE" ]]; then
        echo "Database not found: $DB_FILE" >&2
        exit 1
    fi

    local days="30"
    local dry_run="0"
    for arg in "$@"; do
        if [[ "$arg" == "--dry-run" ]]; then
            dry_run="1"
        elif [[ "$arg" =~ ^[0-9]+$ ]]; then
            days="$arg"
        fi
    done

    $PYTHON_BIN - "$DB_FILE" "$days" "$dry_run" <<'PYEOF'
import sys
import sqlite3
from datetime import datetime, timedelta

db_path = sys.argv[1]
days = int(sys.argv[2])
dry_run = sys.argv[3] == "1"

try:
    from datetime import UTC
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
except ImportError:
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Find entries to prune
cursor.execute("""
    SELECT id, anchor_type, anchor_topic, text, status, superseded_at
    FROM chunks
    WHERE status IN ('deprecated', 'superseded', 'duplicate')
      AND superseded_at < ?
""", (cutoff,))

rows = cursor.fetchall()

if not rows:
    print(f"No entries older than {days} days to prune.")
    sys.exit(0)

print(f"Found {len(rows)} entries to prune (older than {days} days):")
for row in rows:
    cid, ctype, ctopic, text, status, superseded_at = row
    text_short = (text or "")[:60].replace('\n', ' ')
    print(f"  - {cid} [{status}] {ctopic}: {text_short}...")

if dry_run:
    print(f"\n(dry-run: would delete {len(rows)} entries)")
else:
    cursor.execute("""
        DELETE FROM chunks
        WHERE status IN ('deprecated', 'superseded', 'duplicate')
          AND superseded_at < ?
    """, (cutoff,))
    conn.commit()
    print(f"\nDeleted {len(rows)} entries.")

conn.close()
PYEOF
}

cmd_render() {
    # Render memory entries in compact glyph format for LLM consumption
    # Format: [TYPE][topic=TOPIC][ts=YYYY-MM-DD] CONTENT
    # Optional: [choice=X] for decisions
    if [[ ! -f "$DB_FILE" ]]; then
        echo "Database not found: $DB_FILE" >&2
        echo "Run './mem-db.sh init' first." >&2
        exit 1
    fi

    $PYTHON_BIN - "$DB_FILE" "$@" <<'PYEOF'
import sys
import sqlite3

db_path = sys.argv[1]
filters = sys.argv[2:]

def format_relative_time(ts_str):
    """Convert ISO timestamp to relative time + freshness flag."""
    if not ts_str:
        return ("?", False)
    from datetime import datetime, timezone
    ts_str = ts_str.replace('Z', '+00:00')
    try:
        ts = datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        return ("?", False)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - ts
    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        return (ts_str[:10], False)
    is_fresh = total_seconds < 3600  # < 1 hour
    if total_seconds < 60:
        return (f"{int(total_seconds)}s ago", is_fresh)
    elif total_seconds < 3600:
        return (f"{int(total_seconds / 60)}m ago", is_fresh)
    elif total_seconds < 86400:
        return (f"{int(total_seconds / 3600)}h ago", is_fresh)
    elif total_seconds < 2592000:
        return (f"{int(total_seconds / 86400)}d ago", is_fresh)
    else:
        return (ts_str[:10], False)

# Type abbreviation expansion
# Core types: d/q/a/f/n/c (decision/question/action/fact/note/conversation)
# Task types: T/G/M/R/L (todo/goal/attempt/result/lesson) - uppercase to distinguish
def expand_type(t):
    type_map = {
        'd': 'd', 'decision': 'd',
        'q': 'q', 'question': 'q',
        'a': 'a', 'action': 'a',
        'f': 'f', 'fact': 'f',
        'n': 'n', 'note': 'n',
        'c': 'c', 'conversation': 'c',
        # Task-centric types (uppercase)
        't': 'T', 'todo': 'T',
        'g': 'G', 'goal': 'G',
        'm': 'M', 'attempt': 'M',
        'r': 'R', 'result': 'R',
        'l': 'L', 'lesson': 'L'
    }
    return type_map.get(t.lower(), t)

# Type label for output (uppercase single char)
def type_label(t):
    return {
        'd': 'D', 'q': 'Q', 'a': 'A', 'f': 'F', 'n': 'N', 'c': 'C',
        'T': 'T', 'G': 'G', 'M': 'M', 'R': 'R', 'L': 'L'
    }.get(t, '?')

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
    elif key == 'recent':
        import re
        match = re.match(r'^(\d+)([hdwm])$', val.strip().lower())
        if not match:
            print(f"ERROR: Invalid recent format '{val}'. Use: 1h, 24h, 7d, 1w, 1m", file=sys.stderr)
            sys.exit(1)
        amount, unit = int(match.group(1)), match.group(2)
        from datetime import datetime, timedelta, timezone
        delta_map = {'h': timedelta(hours=amount), 'd': timedelta(days=amount), 'w': timedelta(weeks=amount), 'm': timedelta(days=amount*30)}
        cutoff = datetime.now(timezone.utc) - delta_map[unit]
        params['since'] = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')
        where_clauses.append("timestamp >= :since")
    elif key == 'limit':
        limit = int(val)

# Build query
query = """
SELECT
    anchor_type,
    anchor_topic,
    text,
    anchor_choice,
    timestamp
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
    sys.exit(0)

# Output in compact glyph format
for row in results:
    anchor_type, topic, text, choice, ts = row

    # Build glyph header
    t = type_label(anchor_type)
    topic_str = topic or "general"
    ts_rel, is_fresh = format_relative_time(ts)
    fresh_tag = "[FRESH]" if is_fresh else ""

    # Core header: [TYPE][topic=X][ts=YYYY-MM-DD]
    header = f"[{t}][topic={topic_str}][ts={ts_rel}]{fresh_tag}"

    # Add choice for decisions
    if anchor_type == 'd' and choice:
        header += f"[choice={choice}]"

    # Content (strip newlines for compact output)
    content = (text or "").replace('\n', ' ').strip()

    print(f"{header} {content}")
PYEOF
}

cmd_health() {
    if [[ ! -f "$DB_FILE" ]]; then
        echo "Database not found: $DB_FILE" >&2
        echo "Run './mem-db.sh init' first." >&2
        exit 1
    fi

    local anchors_file="$SCRIPT_DIR/anchors.jsonl"

    $PYTHON_BIN - "$DB_FILE" "$anchors_file" <<'PYEOF'
import sys
import os
import sqlite3
from datetime import datetime, timezone, timedelta

db_path = sys.argv[1]
anchors_path = sys.argv[2]

# Database file size
db_size = os.path.getsize(db_path)
if db_size < 1024:
    size_str = f"{db_size}B"
elif db_size < 1024 * 1024:
    size_str = f"{int(db_size / 1024)}K"
else:
    size_str = f"{int(db_size / (1024 * 1024))}M"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Total entries
cursor.execute("SELECT COUNT(*) FROM chunks")
total_entries = cursor.fetchone()[0]

# Embedding coverage
cursor.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
embedded_count = cursor.fetchone()[0]
embedding_pct = int(100 * embedded_count / total_entries) if total_entries > 0 else 0

# Get embedding model info
cursor.execute("""
    SELECT embedding_model, embedding_dim
    FROM chunks
    WHERE embedding IS NOT NULL
    LIMIT 1
""")
emb_info = cursor.fetchone()
if emb_info:
    emb_model, emb_dim = emb_info
else:
    emb_model, emb_dim = "none", 0

# Sync state - compare anchors.jsonl line count with DB count
if os.path.exists(anchors_path):
    with open(anchors_path) as f:
        anchors_count = sum(1 for _ in f)
    sync_status = "synced" if anchors_count == total_entries else f"drift (jsonl={anchors_count}, db={total_entries})"
else:
    sync_status = "no anchors.jsonl"

# Type breakdown
cursor.execute("""
    SELECT anchor_type, COUNT(*)
    FROM chunks
    GROUP BY anchor_type
    ORDER BY COUNT(*) DESC
""")
type_rows = cursor.fetchall()

type_map = {
    'd': 'decisions',
    'q': 'questions',
    'a': 'actions',
    'f': 'facts',
    'n': 'notes',
    'c': 'conversations',
    # Task-centric types
    'T': 'todos',
    'G': 'goals',
    'M': 'attempts',
    'R': 'results',
    'L': 'lessons'
}

type_breakdown = []
for t, count in type_rows:
    if t:
        type_name = type_map.get(t, t)
        type_breakdown.append(f"  {type_name} ({t}): {count}")

# Freshness metrics
cursor.execute("SELECT COUNT(*) FROM chunks WHERE timestamp > datetime('now', '-1 hour')")
fresh_1h = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM chunks WHERE timestamp > datetime('now', '-24 hours')")
fresh_24h = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM chunks WHERE timestamp > datetime('now', '-7 days')")
fresh_7d = cursor.fetchone()[0]

# Most recent entry
cursor.execute("SELECT timestamp FROM chunks ORDER BY timestamp DESC LIMIT 1")
latest_ts = cursor.fetchone()

if latest_ts and latest_ts[0]:
    ts_str = latest_ts[0].replace('Z', '+00:00')
    try:
        ts = datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        ts = None

    if ts:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - ts
        total_seconds = delta.total_seconds()

        if total_seconds < 60:
            time_ago = f"{int(total_seconds)}s ago"
        elif total_seconds < 3600:
            time_ago = f"{int(total_seconds / 60)}m ago"
        elif total_seconds < 86400:
            time_ago = f"{int(total_seconds / 3600)}h ago"
        elif total_seconds < 2592000:  # < 30 days
            time_ago = f"{int(total_seconds / 86400)}d ago"
        else:
            time_ago = latest_ts[0][:10]  # Show date for very old entries

        is_fresh = total_seconds < 3600
        fresh_tag = " [FRESH]" if is_fresh else ""
        last_entry = time_ago + fresh_tag
    else:
        last_entry = latest_ts[0][:10]
else:
    last_entry = "none"

# Top topics
cursor.execute("""
    SELECT anchor_topic, COUNT(*) as cnt
    FROM chunks
    WHERE anchor_topic IS NOT NULL
    GROUP BY anchor_topic
    ORDER BY cnt DESC
    LIMIT 5
""")
top_topics = cursor.fetchall()

conn.close()

# Health status logic
health_status = "GREEN"
health_msg = "all systems nominal"

if total_entries == 0:
    health_status = "RED"
    health_msg = "no entries"
elif sync_status.startswith("drift"):
    health_status = "YELLOW"
    health_msg = "sync drift detected"
elif embedding_pct < 50 and total_entries > 10:
    health_status = "YELLOW"
    health_msg = f"low embedding coverage ({embedding_pct}%)"

# Output dashboard
print("=== Memory Health Dashboard ===")
print(f"Database: memory.db ({size_str}, {total_entries} entries)")
print(f"Embeddings: {embedded_count}/{total_entries} ({embedding_pct}%) [{emb_model}, dim={emb_dim}]")
print(f"Sync: anchors.jsonl → memory.db ({sync_status})")
print()

print("Entry Types:")
if type_breakdown:
    for line in type_breakdown:
        print(line)
else:
    print("  (no entries)")
print()

print("Freshness:")
print(f"  Last entry: {last_entry}")
print(f"  < 1 hour: {fresh_1h} entries")
print(f"  < 24 hours: {fresh_24h} entries")
print(f"  < 7 days: {fresh_7d} entries")
print()

if top_topics:
    topic_strs = [f"{topic} ({count})" for topic, count in top_topics]
    print(f"Top Topics: {', '.join(topic_strs)}")
else:
    print("Top Topics: (none)")
print()

# Health status with color
if health_status == "GREEN":
    status_color = "\033[1;32m"  # bright green
    status_symbol = "✓"
elif health_status == "YELLOW":
    status_color = "\033[1;33m"  # bright yellow
    status_symbol = "⚠"
else:  # RED
    status_color = "\033[1;31m"  # bright red
    status_symbol = "✗"

print(f"Health: {status_color}{status_symbol} {health_status}\033[0m ({health_msg})")
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
        embed) cmd_embed "$@" ;;
        semantic) cmd_semantic "$@" ;;
        topic-index) cmd_topic_index "$@" ;;
        consolidate) cmd_consolidate "$@" ;;
        prune) cmd_prune "$@" ;;
        render) cmd_render "$@" ;;
        health) cmd_health "$@" ;;
        recent) cmd_query "recent=24h" "limit=10" "$@" ;;
        *) echo "Usage: $0 {init|migrate|sync|status|query|write|embed|semantic|topic-index|consolidate|prune|render|health|recent}" >&2; exit 1 ;;
    esac
}

main "$@"
