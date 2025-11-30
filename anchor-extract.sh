#!/usr/bin/env bash
#
# anchor-extract.sh - Extract anchors from session logs using LLM
#
# Usage:
#   ./anchor-extract.sh session.log
#   ./anchor-extract.sh session.log --dry-run
#   cat log.txt | ./anchor-extract.sh -
#
# Options:
#   --dry-run         Show extracted anchors without appending
#   --model MODEL     LLM to use: claude (default), codex
#   --session ID      Session ID to tag anchors (default: from filename)
#   --source SOURCE   Source tag (default: log-extract)
#
# Output:
#   Appends extracted anchors to anchors.jsonl (unless --dry-run)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANCHORS_FILE="${ANCHORS_FILE:-$SCRIPT_DIR/anchors.jsonl}"

# Defaults
DRY_RUN=0
MODEL="claude"
SESSION=""
SOURCE="log-extract"
INPUT_FILE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --session)
            SESSION="$2"
            shift 2
            ;;
        --source)
            SOURCE="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            INPUT_FILE="$1"
            shift
            ;;
    esac
done

# Read input
if [[ -z "$INPUT_FILE" ]]; then
    echo "Usage: $0 [options] <logfile|->" >&2
    exit 1
fi

if [[ "$INPUT_FILE" == "-" ]]; then
    LOG_CONTENT=$(cat)
    [[ -z "$SESSION" ]] && SESSION="stdin_$(date +%Y%m%d_%H%M%S)"
else
    if [[ ! -f "$INPUT_FILE" ]]; then
        echo "File not found: $INPUT_FILE" >&2
        exit 1
    fi
    LOG_CONTENT=$(cat "$INPUT_FILE")
    [[ -z "$SESSION" ]] && SESSION=$(basename "$INPUT_FILE" | sed 's/\.[^.]*$//')
fi

# Extraction prompt
EXTRACT_PROMPT=$(cat <<'EOF'
Extract key anchors from this session log. Output ONLY valid JSONL arrays.

Anchor format: [type, topic, text, choice, rationale, timestamp, session, source]
- type: "d" (decision), "q" (question), "a" (action), "f" (fact), "n" (note)
- topic: 1-2 word category
- text: concise summary (1 sentence)
- choice: the decision made (for d type) or empty string
- rationale: why this choice (for d type) or empty string
- timestamp: ISO format or empty string if unknown
- session: "SESSION_ID"
- source: "SOURCE_TAG"

Rules:
1. Only extract HIGH-SIGNAL anchors (decisions made, questions asked, actions taken, important facts)
2. Skip routine operations, confirmations, error recovery
3. Maximum 10 anchors per log
4. Output raw JSONL only, no markdown, no explanations

Example output:
["d","api","Use REST over GraphQL","REST","Simpler for MVP","2025-11-29T10:00:00Z","SESSION_ID","SOURCE_TAG"]
["q","auth","Should we use JWT or sessions?","","","2025-11-29T10:15:00Z","SESSION_ID","SOURCE_TAG"]

---
SESSION_ID: SESSION_PLACEHOLDER
SOURCE_TAG: SOURCE_PLACEHOLDER

LOG:
EOF
)

# Replace placeholders
EXTRACT_PROMPT="${EXTRACT_PROMPT//SESSION_PLACEHOLDER/$SESSION}"
EXTRACT_PROMPT="${EXTRACT_PROMPT//SOURCE_PLACEHOLDER/$SOURCE}"
EXTRACT_PROMPT="$EXTRACT_PROMPT"$'\n'"$LOG_CONTENT"

# Extract using LLM
extract_anchors() {
    case "$MODEL" in
        claude)
            claude -p "$EXTRACT_PROMPT" 2>/dev/null
            ;;
        codex)
            codex exec -m gpt-5.1-codex-max -c 'model_reasoning_effort="medium"' --full-auto "$EXTRACT_PROMPT" 2>/dev/null
            ;;
        *)
            echo "Unknown model: $MODEL" >&2
            exit 1
            ;;
    esac
}

main() {
    echo "Extracting anchors from: ${INPUT_FILE:-stdin}" >&2
    echo "Session: $SESSION | Source: $SOURCE" >&2

    local extracted
    extracted=$(extract_anchors)

    # Filter to valid JSONL lines only
    local valid_anchors
    valid_anchors=$(echo "$extracted" | grep '^\[' | while IFS= read -r line; do
        if echo "$line" | jq empty 2>/dev/null; then
            echo "$line"
        fi
    done)

    if [[ -z "$valid_anchors" ]]; then
        echo "No valid anchors extracted." >&2
        exit 0
    fi

    local count
    count=$(echo "$valid_anchors" | wc -l)
    echo "Extracted $count anchors:" >&2

    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "=== DRY RUN (not saved) ===" >&2
        echo "$valid_anchors"
    else
        echo "$valid_anchors" >> "$ANCHORS_FILE"
        echo "Appended to: $ANCHORS_FILE" >&2
        echo "$valid_anchors"
    fi
}

main
