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
#   --role ROLE       Agent role: architect, coder, reviewer, pm (affects prompt + memory scope)
#   --chat-id ID      Chat identifier for scoped memory
#   --dry-run         Show prompt without executing
#   --no-memory       Skip memory injection (pass-through)
#
# Environment:
#   MEMORY_DIR        Override memory directory (default: script dir)
#   MEM_FILTERS       Default filters if --filters not specified
#
# Roles:
#   architect  - System design, architecture decisions, high-level planning
#   coder      - Implementation, code writing, debugging
#   reviewer   - Code review, quality assurance, testing
#   pm         - Project management, requirements, coordination
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMORY_DIR="${MEMORY_DIR:-$SCRIPT_DIR}"

# Defaults
FILTERS="${MEM_FILTERS:-}"
LIMIT=10
MODEL="claude"
ROLE=""
CHAT_ID=""
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
        --role)
            ROLE="$2"
            shift 2
            ;;
        --chat-id)
            CHAT_ID="$2"
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

# Role-specific system prompts
get_role_prompt() {
    local role="$1"
    case "$role" in
        architect)
            cat <<'ROLE_PROMPT'
You are a software architect. Focus on:
- System design and high-level architecture
- Technology choices and trade-offs
- Scalability, maintainability, security patterns
- Interface definitions and module boundaries
Record architectural decisions as type=d (decisions).
ROLE_PROMPT
            ;;
        coder)
            cat <<'ROLE_PROMPT'
You are a software developer. Focus on:
- Implementation details and code quality
- Debugging and problem-solving
- Following established patterns and conventions
- Writing clean, testable code
Record implementation notes as type=f (facts) or type=n (notes).
ROLE_PROMPT
            ;;
        reviewer)
            cat <<'ROLE_PROMPT'
You are a code reviewer. Focus on:
- Code quality, correctness, and best practices
- Security vulnerabilities and edge cases
- Performance implications
- Test coverage and documentation
Record review findings as type=f (facts) or type=q (questions).
ROLE_PROMPT
            ;;
        pm)
            cat <<'ROLE_PROMPT'
You are a project manager. Focus on:
- Requirements gathering and clarification
- Task breakdown and prioritization
- Coordination between team members
- Progress tracking and blockers
Record requirements and tasks as type=a (actions) or type=q (questions).
ROLE_PROMPT
            ;;
        *)
            # No role-specific prompt
            return
            ;;
    esac
}

# Build memory context using compact glyph format
# Scoping rules:
#   1. Shared + public: visible to all roles/chats
#   2. Chat-scoped: only visible within same chat_id
#   3. Role entries: only public ones from same role (not private/internal from other chats)
build_memory_context() {
    local mem_db="$MEMORY_DIR/mem-db.sh"

    if [[ ! -x "$mem_db" ]]; then
        echo "# (memory not available)" >&2
        return
    fi

    local output=""

    # 1. Shared scope, public visibility (universal access)
    local shared_decisions
    shared_decisions=$($mem_db render t=d scope=shared visibility=public limit=5 2>/dev/null || true)
    if [[ -n "$shared_decisions" ]]; then
        output+="$shared_decisions"$'\n'
    fi

    local shared_questions
    shared_questions=$($mem_db render t=q scope=shared visibility=public limit=3 2>/dev/null || true)
    if [[ -n "$shared_questions" ]]; then
        output+="$shared_questions"$'\n'
    fi

    # 2. Chat-scoped entries (if chat_id provided)
    if [[ -n "$CHAT_ID" ]]; then
        local chat_entries
        chat_entries=$($mem_db render scope=chat chat_id="$CHAT_ID" limit=5 2>/dev/null || true)
        if [[ -n "$chat_entries" ]]; then
            output+="$chat_entries"$'\n'
        fi
    fi

    # 3. Role-specific: only public entries from same role
    if [[ -n "$ROLE" ]]; then
        local role_entries
        role_entries=$($mem_db render role="$ROLE" visibility=public limit=5 2>/dev/null || true)
        if [[ -n "$role_entries" ]]; then
            output+="$role_entries"$'\n'
        fi

        # Also get internal entries for this role IF we have a chat_id (same session)
        if [[ -n "$CHAT_ID" ]]; then
            local role_internal
            role_internal=$($mem_db render role="$ROLE" visibility=internal chat_id="$CHAT_ID" limit=3 2>/dev/null || true)
            if [[ -n "$role_internal" ]]; then
                output+="$role_internal"$'\n'
            fi
        fi
    fi

    # Custom filters if provided (user responsibility for scoping)
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
    local role_prompt=""

    if [[ "$NO_MEMORY" -eq 0 ]]; then
        memory_block=$(build_memory_context)
    fi

    if [[ -n "$ROLE" ]]; then
        role_prompt=$(get_role_prompt "$ROLE")
    fi

    # Start with role prompt if set
    if [[ -n "$role_prompt" ]]; then
        echo "# Role: ${ROLE^^}"
        echo "$role_prompt"
        echo
    fi

    if [[ -n "$memory_block" ]]; then
        echo "# Memory Context (retrieved: $(date -Iseconds))"
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
