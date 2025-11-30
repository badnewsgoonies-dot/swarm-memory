#!/usr/bin/env bash
#
# head-with-memory.sh - Inject relevant memory into Claude/Codex prompts
#
# Usage:
#   ./head-with-memory.sh "Your query here"
#   ./head-with-memory.sh --filters "t=d topic=memory" "Your query"
#   echo "query" | ./head-with-memory.sh
#
# Options:
#   --filters "..."   Custom mem-search filters (default: recent decisions + open questions)
#   --limit N         Max memory entries to inject (default: 10)
#   --model MODEL     LLM to use: claude (default), codex
#   --dry-run         Show prompt without executing
#   --no-memory       Skip memory injection (pass-through)
#
# Environment:
#   MEMORY_DIR        Override memory directory (default: script dir)
#   MEM_FILTERS       Default filters if --filters not specified
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMORY_DIR="${MEMORY_DIR:-$SCRIPT_DIR}"

# Defaults
FILTERS="${MEM_FILTERS:-}"
LIMIT=10
MODEL="claude"
DRY_RUN=0
NO_MEMORY=0
QUERY=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --filters)
            FILTERS="$2"
            shift 2
            ;;
        --limit)
            LIMIT="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --no-memory)
            NO_MEMORY=1
            shift
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            QUERY="$1"
            shift
            ;;
    esac
done

# Read from stdin if no query provided
if [[ -z "$QUERY" ]]; then
    QUERY=$(cat)
fi

if [[ -z "$QUERY" ]]; then
    echo "Usage: $0 [options] \"query\"" >&2
    exit 1
fi

# Build memory context
build_memory_context() {
    local mem_search="$MEMORY_DIR/mem-search.sh"

    if [[ ! -x "$mem_search" ]]; then
        echo "# (memory search not available)" >&2
        return
    fi

    # Get recent decisions
    local decisions
    decisions=$($mem_search t=d limit=5 --json 2>/dev/null || true)
    if [[ -n "$decisions" ]]; then
        echo "## Recent Decisions"
        while IFS= read -r line; do
            local topic text choice
            topic=$(echo "$line" | jq -r '.[1]')
            text=$(echo "$line" | jq -r '.[2]')
            choice=$(echo "$line" | jq -r '.[3] // empty')
            if [[ -n "$choice" ]]; then
                echo "- **$topic**: $text → *$choice*"
            else
                echo "- **$topic**: $text"
            fi
        done <<< "$decisions"
        echo
    fi

    # Get open questions
    local questions
    questions=$($mem_search t=q limit=3 --json 2>/dev/null || true)
    if [[ -n "$questions" ]]; then
        echo "## Open Questions"
        while IFS= read -r line; do
            local topic text
            topic=$(echo "$line" | jq -r '.[1]')
            text=$(echo "$line" | jq -r '.[2]')
            echo "- **$topic**: $text"
        done <<< "$questions"
        echo
    fi

    # Custom filters if provided
    if [[ -n "$FILTERS" ]]; then
        local custom
        # shellcheck disable=SC2086
        custom=$($mem_search $FILTERS limit="$LIMIT" --json 2>/dev/null || true)
        if [[ -n "$custom" ]]; then
            echo "## Relevant Context"
            while IFS= read -r line; do
                local t topic text
                t=$(echo "$line" | jq -r '.[0]')
                topic=$(echo "$line" | jq -r '.[1]')
                text=$(echo "$line" | jq -r '.[2]')
                echo "- [$t] **$topic**: $text"
            done <<< "$custom"
            echo
        fi
    fi
}

# Build full prompt
build_prompt() {
    local memory_block=""

    if [[ "$NO_MEMORY" -eq 0 ]]; then
        memory_block=$(build_memory_context)
    fi

    if [[ -n "$memory_block" ]]; then
        cat <<EOF
# Memory Context
$memory_block
---

$QUERY
EOF
    else
        echo "$QUERY"
    fi
}

# Execute with chosen model
execute_prompt() {
    local prompt="$1"

    case "$MODEL" in
        claude)
            claude -p "$prompt"
            ;;
        codex)
            codex exec -m gpt-5.1-codex-max --full-auto "$prompt"
            ;;
        *)
            echo "Unknown model: $MODEL" >&2
            exit 1
            ;;
    esac
}

main() {
    local prompt
    prompt=$(build_prompt)

    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "=== PROMPT (dry-run) ==="
        echo "$prompt"
        echo "========================"
    else
        execute_prompt "$prompt"
    fi
}

main
