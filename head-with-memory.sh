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

# Build memory context using compact glyph format
build_memory_context() {
    local mem_db="$MEMORY_DIR/mem-db.sh"

    if [[ ! -x "$mem_db" ]]; then
        echo "# (memory not available)" >&2
        return
    fi

    local output=""

    # Get recent decisions (glyphs)
    local decisions
    decisions=$($mem_db render t=d limit=5 2>/dev/null || true)
    if [[ -n "$decisions" ]]; then
        output+="$decisions"$'\n'
    fi

    # Get open questions (glyphs)
    local questions
    questions=$($mem_db render t=q limit=3 2>/dev/null || true)
    if [[ -n "$questions" ]]; then
        output+="$questions"$'\n'
    fi

    # Custom filters if provided
    if [[ -n "$FILTERS" ]]; then
        local custom
        # shellcheck disable=SC2086
        custom=$($mem_db render $FILTERS limit="$LIMIT" 2>/dev/null || true)
        if [[ -n "$custom" ]]; then
            output+="$custom"$'\n'
        fi
    fi

    # Output if we have content
    if [[ -n "$output" ]]; then
        echo "$output"
    fi
}

# Build full prompt with glyph format and examples
build_prompt() {
    local memory_block=""

    if [[ "$NO_MEMORY" -eq 0 ]]; then
        memory_block=$(build_memory_context)
    fi

    if [[ -n "$memory_block" ]]; then
        cat <<'GLYPH_HEADER'
# Memory Glyphs
Format: [TYPE][topic=X][ts=DATE][choice=Y] content
Types: D=decision, Q=question, F=fact, A=action, N=note

GLYPH_HEADER
        echo "$memory_block"
        cat <<EOF
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
