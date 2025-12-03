#!/usr/bin/env bash
#
# mem-orchestrate.sh - Convenience wrapper for running orchestrated tasks
#
# Usage:
#   ./mem-orchestrate.sh <task-id> [repo-root] [options]
#
# Options:
#   --max-iterations N   Max daemon iterations (default: 50)
#   --llm PROVIDER       LLM provider: claude, codex, hybrid, ollama, openai
#   --restricted         Don't use --unrestricted mode (default: unrestricted)
#   --verbose            Enable verbose logging
#   --dry-run            Show what would be run without executing
#
# Examples:
#   ./mem-orchestrate.sh vv2-orch-001 /path/to/vale-village-v2
#   ./mem-orchestrate.sh vv2-orch-001 --llm claude --verbose
#   ./mem-orchestrate.sh vv2-orch-001 /path/to/repo --max-iterations 30
#
# The script:
#   1. Looks up the TODO text for the given task-id
#   2. Ensures the objective has ORCHESTRATE: prefix
#   3. Runs swarm_daemon.py with appropriate flags
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_FILE="${MEMORY_DB:-$SCRIPT_DIR/memory.db}"
DAEMON_SCRIPT="$SCRIPT_DIR/swarm_daemon.py"

# Default options
MAX_ITERATIONS=50
LLM_PROVIDER="claude"
UNRESTRICTED="--unrestricted"
VERBOSE=""
DRY_RUN=false
REPO_ROOT=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    head -28 "$0" | tail -26 | sed 's/^# //' | sed 's/^#//'
    exit 1
}

error() {
    echo -e "${RED}ERROR:${NC} $1" >&2
    exit 1
}

info() {
    echo -e "${GREEN}INFO:${NC} $1"
}

warn() {
    echo -e "${YELLOW}WARN:${NC} $1"
}

# Parse arguments
if [[ $# -lt 1 ]]; then
    usage
fi

TASK_ID="$1"
shift

# Parse remaining arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-iterations)
            MAX_ITERATIONS="$2"
            shift 2
            ;;
        --llm)
            LLM_PROVIDER="$2"
            shift 2
            ;;
        --restricted)
            UNRESTRICTED=""
            shift
            ;;
        --verbose)
            VERBOSE="--verbose"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        -*)
            error "Unknown option: $1"
            ;;
        *)
            # Assume it's the repo root if not an option
            if [[ -z "$REPO_ROOT" ]]; then
                REPO_ROOT="$1"
            else
                error "Unexpected argument: $1"
            fi
            shift
            ;;
    esac
done

# Check database exists
if [[ ! -f "$DB_FILE" ]]; then
    error "Database not found: $DB_FILE. Run ./mem-db.sh init first."
fi

# Look up the TODO text
info "Looking up task: $TASK_ID"

TODO_TEXT=$(python3 << PYEOF
import sqlite3
import json
import sys

conn = sqlite3.connect("$DB_FILE")
cursor = conn.cursor()

# Query for TODO with this task_id
cursor.execute("""
    SELECT text, anchor_topic, links
    FROM chunks
    WHERE anchor_type = 'T'
      AND (
        links LIKE '%"id":"$TASK_ID"%'
        OR links LIKE '%"id": "$TASK_ID"%'
      )
    ORDER BY timestamp DESC
    LIMIT 1
""")

row = cursor.fetchone()
conn.close()

if not row:
    print("NOT_FOUND", file=sys.stderr)
    sys.exit(1)

text, topic, links = row
print(text)
PYEOF
)

if [[ -z "$TODO_TEXT" || "$TODO_TEXT" == "NOT_FOUND" ]]; then
    error "Task not found: $TASK_ID"
fi

info "Found TODO: ${TODO_TEXT:0:80}..."

# Build the objective - ensure it has ORCHESTRATE: prefix
if [[ "$TODO_TEXT" != ORCHESTRATE:* ]]; then
    # Try to extract topic from the TODO
    TOPIC=$(python3 << PYEOF
import sqlite3
import json

conn = sqlite3.connect("$DB_FILE")
cursor = conn.cursor()

cursor.execute("""
    SELECT anchor_topic
    FROM chunks
    WHERE anchor_type = 'T'
      AND (
        links LIKE '%"id":"$TASK_ID"%'
        OR links LIKE '%"id": "$TASK_ID"%'
      )
    ORDER BY timestamp DESC
    LIMIT 1
""")

row = cursor.fetchone()
conn.close()

print(row[0] if row and row[0] else "orchestration")
PYEOF
)
    OBJECTIVE="ORCHESTRATE: [task_id:$TASK_ID] [topic:$TOPIC] $TODO_TEXT"
    info "Added ORCHESTRATE prefix"
else
    OBJECTIVE="$TODO_TEXT"
fi

# Determine repo root if not provided
if [[ -z "$REPO_ROOT" ]]; then
    # Try to infer from topic
    TOPIC=$(echo "$OBJECTIVE" | grep -oP '\[topic:([^\]]+)\]' | sed 's/\[topic://' | sed 's/\]//' || echo "")

    # Default paths based on common topics
    case "$TOPIC" in
        vv2*|VV2*)
            REPO_ROOT="${HOME}/Documents/vale-village-v2"
            ;;
        vv*|VV*)
            REPO_ROOT="${HOME}/Documents/vale-village"
            ;;
        *)
            REPO_ROOT="$SCRIPT_DIR"
            ;;
    esac

    warn "No repo root specified, using: $REPO_ROOT"
fi

# Validate repo root exists
if [[ ! -d "$REPO_ROOT" ]]; then
    error "Repo root not found: $REPO_ROOT"
fi

# Build command
CMD=(
    python3 "$DAEMON_SCRIPT"
    --objective "$OBJECTIVE"
    --repo-root "$REPO_ROOT"
    --max-iterations "$MAX_ITERATIONS"
    --llm "$LLM_PROVIDER"
)

if [[ -n "$UNRESTRICTED" ]]; then
    CMD+=("$UNRESTRICTED")
fi

if [[ -n "$VERBOSE" ]]; then
    CMD+=("$VERBOSE")
fi

# Show or run
echo ""
info "Running orchestration:"
echo "  Task ID:        $TASK_ID"
echo "  Repo:           $REPO_ROOT"
echo "  Max iterations: $MAX_ITERATIONS"
echo "  LLM:            $LLM_PROVIDER"
echo "  Mode:           ${UNRESTRICTED:-restricted}"
echo ""

if $DRY_RUN; then
    info "DRY RUN - would execute:"
    echo "${CMD[@]}"
    exit 0
fi

# Mark task as IN_PROGRESS
"$SCRIPT_DIR/mem-todo.sh" update "$TASK_ID" --status IN_PROGRESS 2>/dev/null || true

info "Starting daemon..."
echo ""

# Run the daemon
exec "${CMD[@]}"
