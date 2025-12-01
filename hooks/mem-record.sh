#!/usr/bin/env bash
#
# mem-record.sh - Claude Code hook to record file changes to memory
#
# Receives JSON on stdin with tool_name, tool_input, tool_response
# Records "interesting" file changes to memory database
#
# To enable, add to ~/.claude/settings.json:
#   "hooks": {
#     "PostToolUse": [
#       {
#         "matcher": "Write|Edit",
#         "hooks": [{ "type": "command", "command": "/home/geni/swarm/memory/hooks/mem-record.sh" }]
#       }
#     ]
#   }

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEM_DB="${SCRIPT_DIR}/../mem-db.sh"
LOG_FILE="${SCRIPT_DIR}/../hooks.log"

# Log function
log() {
    echo "[$(date -Iseconds)] $*" >> "$LOG_FILE"
}

# Read JSON from stdin
INPUT=$(cat)

if [[ -z "$INPUT" ]]; then
    log "ERROR: No input received on stdin"
    exit 0  # Don't block Claude
fi

# Extract fields using jq
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null || true)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)

if [[ -z "$FILE_PATH" ]]; then
    log "SKIP: No file_path in input (tool=$TOOL_NAME)"
    exit 0
fi

# === FILTERING ===

# Skip paths we don't care about
SKIP_PATTERNS=(
    "node_modules"
    "dist/"
    ".git/"
    "package-lock.json"
    "pnpm-lock.yaml"
    "yarn.lock"
    ".next/"
    ".cache/"
    "__pycache__"
    ".pyc"
    ".map"
    ".min.js"
    ".min.css"
)

for pattern in "${SKIP_PATTERNS[@]}"; do
    if [[ "$FILE_PATH" == *"$pattern"* ]]; then
        log "SKIP: Matches skip pattern '$pattern': $FILE_PATH"
        exit 0
    fi
done

# Only record certain file types
INTERESTING_EXTENSIONS=(
    ".ts" ".tsx" ".js" ".jsx"
    ".py" ".rs" ".go"
    ".json" ".yaml" ".yml" ".toml"
    ".md" ".mdx"
    ".css" ".scss"
    ".sh" ".bash"
    ".sql"
)

FILE_EXT=".${FILE_PATH##*.}"
IS_INTERESTING=false

for ext in "${INTERESTING_EXTENSIONS[@]}"; do
    if [[ "$FILE_EXT" == "$ext" ]]; then
        IS_INTERESTING=true
        break
    fi
done

if [[ "$IS_INTERESTING" != "true" ]]; then
    log "SKIP: Not interesting extension ($FILE_EXT): $FILE_PATH"
    exit 0
fi

# === DETERMINE ACTION TYPE ===

ACTION_TYPE="edit"
if [[ "$TOOL_NAME" == "Write" ]]; then
    ACTION_TYPE="create"
fi

# Extract relative path for cleaner display
REL_PATH="$FILE_PATH"
for root in "/home/geni/Documents/vale-village-v2" "/home/geni/Documents/vale-village" "/home/geni/swarm/memory"; do
    if [[ "$FILE_PATH" == "$root"* ]]; then
        REL_PATH="${FILE_PATH#$root/}"
        break
    fi
done

# === DETERMINE TOPIC ===

TOPIC="file-change"
if [[ "$REL_PATH" == src/ui/* ]]; then
    TOPIC="ui"
elif [[ "$REL_PATH" == src/core/* ]]; then
    TOPIC="core"
elif [[ "$REL_PATH" == src/state/* ]]; then
    TOPIC="state"
elif [[ "$REL_PATH" == src/data/* ]]; then
    TOPIC="data"
elif [[ "$REL_PATH" == *.test.* ]] || [[ "$REL_PATH" == *_test.* ]] || [[ "$REL_PATH" == tests/* ]]; then
    TOPIC="test"
elif [[ "$REL_PATH" == *.md ]]; then
    TOPIC="docs"
elif [[ "$REL_PATH" == *.json ]] || [[ "$REL_PATH" == *.yaml ]] || [[ "$REL_PATH" == *.toml ]]; then
    TOPIC="config"
fi

# Build memory text
TEXT="${ACTION_TYPE^}: $REL_PATH"

# Build session arg
SESSION_ARG=""
if [[ -n "$SESSION_ID" ]]; then
    SESSION_ARG="session=${SESSION_ID:0:8}"
fi

# Record to memory (run async to not block Claude)
(
    "$MEM_DB" write \
        t=a \
        topic="$TOPIC" \
        text="$TEXT" \
        ${SESSION_ARG:+$SESSION_ARG} \
        source=hook \
        2>&1 | head -1 >> "$LOG_FILE"
) &

log "RECORDED: $TEXT (topic=$TOPIC)"
exit 0
