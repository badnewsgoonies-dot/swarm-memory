#!/usr/bin/env bash
#
# msg-user.sh - Capture user prompts to memory
#
# Hook event: UserPromptSubmit
# Receives JSON: {"prompt": "...", "session_id": "...", ...}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEM_DB="${SCRIPT_DIR}/../mem-db.sh"
LOG_FILE="${SCRIPT_DIR}/../hooks.log"

log() { echo "[$(date -Iseconds)] [msg-user] $*" >> "$LOG_FILE"; }

# Read JSON from stdin
INPUT=$(cat)
[[ -z "$INPUT" ]] && exit 0

# Extract fields
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

[[ -z "$PROMPT" ]] && { log "SKIP: No prompt"; exit 0; }

# === FILTERING ===

# Check length (minimum 20 chars)
PROMPT_LEN=${#PROMPT}
[[ $PROMPT_LEN -lt 20 ]] && { log "SKIP: Too short ($PROMPT_LEN chars)"; exit 0; }

# Check for trivial patterns (case-insensitive)
PROMPT_LOWER=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]' | xargs)
TRIVIAL_PATTERNS=(
    "^(ok|okay|k|kk)$"
    "^(hi|hello|hey)$"
    "^(thanks|thank you|thx|ty)$"
    "^(yes|no|yep|nope|yeah|nah)$"
    "^(sure|fine|great|good|nice|cool|awesome)$"
    "^(got it|understood|i see)$"
    "^(continue|go ahead|proceed|go|do it)$"
    "^(lgtm|looks good)$"
)
for pattern in "${TRIVIAL_PATTERNS[@]}"; do
    if echo "$PROMPT_LOWER" | grep -qE "$pattern"; then
        log "SKIP: Trivial pattern '$pattern'"
        exit 0
    fi
done

# Word count check (minimum 3 words)
WORD_COUNT=$(echo "$PROMPT" | wc -w)
[[ $WORD_COUNT -lt 3 ]] && { log "SKIP: Too few words ($WORD_COUNT)"; exit 0; }

# === CONTENT PROCESSING ===

# Truncate to max 2000 chars for storage
MAX_LEN=2000
if [[ $PROMPT_LEN -gt $MAX_LEN ]]; then
    PROMPT="${PROMPT:0:$MAX_LEN}..."
fi

# Escape for shell safety
PROMPT_ESCAPED=$(printf '%s' "$PROMPT" | sed "s/'/'\\\\''/g")

# === RECORD TO MEMORY ===

SESSION_SHORT="${SESSION_ID:0:8}"
(
    "$MEM_DB" write \
        t=c \
        topic="conversation" \
        text="$PROMPT_ESCAPED" \
        choice="user" \
        session="$SESSION_SHORT" \
        source="hook-UserPromptSubmit" \
        scope="chat" \
        chat_id="$SESSION_ID" \
        2>&1 | head -1 >> "$LOG_FILE"
) &

log "RECORDED: User message (${PROMPT_LEN} chars, ${WORD_COUNT} words, session=$SESSION_SHORT)"
exit 0
