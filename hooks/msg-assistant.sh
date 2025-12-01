#!/usr/bin/env bash
#
# msg-assistant.sh - Capture assistant responses to memory
#
# Hook event: Stop
# Receives JSON: {"session_id": "...", "transcript_path": "...", ...}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEM_DB="${SCRIPT_DIR}/../mem-db.sh"
LOG_FILE="${SCRIPT_DIR}/../hooks.log"

log() { echo "[$(date -Iseconds)] [msg-assistant] $*" >> "$LOG_FILE"; }

# Read JSON from stdin
INPUT=$(cat)
[[ -z "$INPUT" ]] && exit 0

# Extract fields
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')

[[ -z "$TRANSCRIPT_PATH" ]] && { log "SKIP: No transcript path"; exit 0; }
[[ ! -f "$TRANSCRIPT_PATH" ]] && { log "SKIP: Transcript not found: $TRANSCRIPT_PATH"; exit 0; }

# === EXTRACT LAST ASSISTANT MESSAGE ===

# Find the last assistant message in transcript (JSONL format)
# Look for entries with type "assistant" or role "assistant"
LAST_ASSISTANT=$(tac "$TRANSCRIPT_PATH" 2>/dev/null | \
    grep -m1 '"role":\s*"assistant"' || true)

[[ -z "$LAST_ASSISTANT" ]] && { log "SKIP: No assistant message found"; exit 0; }

# Extract text content from assistant message
# The content is an array of blocks, we want type="text" blocks
RESPONSE=$(echo "$LAST_ASSISTANT" | jq -r '
    .message.content[]? // .content[]? |
    select(.type == "text") |
    .text // empty
' 2>/dev/null | head -1)

# Fallback: try direct text extraction
if [[ -z "$RESPONSE" ]]; then
    RESPONSE=$(echo "$LAST_ASSISTANT" | jq -r '.message.content[0].text // .content[0].text // empty' 2>/dev/null)
fi

[[ -z "$RESPONSE" ]] && { log "SKIP: Could not extract response text"; exit 0; }

# === FILTERING ===

# Check length (minimum 100 chars for assistant responses)
RESPONSE_LEN=${#RESPONSE}
[[ $RESPONSE_LEN -lt 100 ]] && { log "SKIP: Too short ($RESPONSE_LEN chars)"; exit 0; }

# Word count check (minimum 15 words)
WORD_COUNT=$(echo "$RESPONSE" | wc -w)
[[ $WORD_COUNT -lt 15 ]] && { log "SKIP: Too few words ($WORD_COUNT)"; exit 0; }

# === CONTENT PROCESSING ===

# Extract a summary/excerpt (prefer ending summary or first meaningful paragraph)
MAX_LEN=4000
EXCERPT="$RESPONSE"

# Look for summary markers and extract from there
if echo "$RESPONSE" | grep -qiE "(## summary|in summary|to summarize|### done|completed:|finished:)"; then
    # Extract from the summary marker onwards
    SUMMARY_PART=$(echo "$RESPONSE" | sed -n '/[Ss]ummary\|[Dd]one\|[Cc]ompleted\|[Ff]inished/,$p' | head -c $MAX_LEN)
    if [[ -n "$SUMMARY_PART" && ${#SUMMARY_PART} -gt 50 ]]; then
        EXCERPT="$SUMMARY_PART"
    fi
fi

# Truncate if needed
if [[ ${#EXCERPT} -gt $MAX_LEN ]]; then
    EXCERPT="${EXCERPT:0:$MAX_LEN}..."
fi

# Escape for shell safety
EXCERPT_ESCAPED=$(printf '%s' "$EXCERPT" | sed "s/'/'\\\\''/g")

# === RECORD TO MEMORY ===

SESSION_SHORT="${SESSION_ID:0:8}"
(
    "$MEM_DB" write \
        t=c \
        topic="conversation" \
        text="$EXCERPT_ESCAPED" \
        choice="assistant" \
        session="$SESSION_SHORT" \
        source="hook-Stop" \
        scope="chat" \
        chat_id="$SESSION_ID" \
        2>&1 | head -1 >> "$LOG_FILE"
) &

log "RECORDED: Assistant message (${#EXCERPT} chars, session=$SESSION_SHORT)"
exit 0
