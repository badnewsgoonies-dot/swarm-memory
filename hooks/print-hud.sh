#!/usr/bin/env bash
#
# print-hud.sh - Heads-Up Display (HUD) for Swarm Memory
#
# Prints a visually distinct banner showing:
# - Top 5 OPEN Tasks (TODOs/GOALs)
# - Critical/MANDATE importance memories
#
# Usage:
#   ./print-hud.sh              # Print HUD to stdout
#   ./print-hud.sh --json       # Output as JSON for programmatic use
#   ./print-hud.sh --compact    # Single-line compact format
#
# Output is designed for injection at the top of LLM prompts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEM_DB="${SCRIPT_DIR}/../mem-db.sh"

# Parse args
JSON_OUTPUT=0
COMPACT=0
for arg in "$@"; do
    case "$arg" in
        --json) JSON_OUTPUT=1 ;;
        --compact) COMPACT=1 ;;
    esac
done

# =============================================================================
# FETCH DATA
# =============================================================================

# Fetch top 5 OPEN TODOs (anchor_type=T, anchor_choice=OPEN)
OPEN_TODOS=$("$MEM_DB" query t=T choice=OPEN limit=5 --json 2>/dev/null || echo "")

# Fetch top 5 OPEN GOALs (anchor_type=G, anchor_choice=OPEN)
OPEN_GOALS=$("$MEM_DB" query t=G choice=OPEN limit=5 --json 2>/dev/null || echo "")

# Fetch Critical/High importance memories (any type) - "MANDATE" level
# These are the "immortal" memories that should always be visible
CRITICAL_MEMORIES=$("$MEM_DB" query importance=h,critical,high limit=5 --json 2>/dev/null || echo "")
# Also search for MANDATE keyword in text for backwards compatibility
MANDATE_MEMORIES=$("$MEM_DB" query text=MANDATE limit=5 --json 2>/dev/null || echo "")

# =============================================================================
# JSON OUTPUT
# =============================================================================

if [[ "$JSON_OUTPUT" == "1" ]]; then
    cat <<EOF
{
  "hud_version": "1.0",
  "timestamp": "$(date -Iseconds)",
  "open_tasks": {
    "todos": $(echo "$OPEN_TODOS" | grep -v "^$" | head -5 | jq -s '.' 2>/dev/null || echo "[]"),
    "goals": $(echo "$OPEN_GOALS" | grep -v "^$" | head -5 | jq -s '.' 2>/dev/null || echo "[]")
  },
  "critical_memories": $(echo "$CRITICAL_MEMORIES
$MANDATE_MEMORIES" | grep -v "^$" | head -5 | jq -s '.' 2>/dev/null || echo "[]")
}
EOF
    exit 0
fi

# =============================================================================
# TEXT OUTPUT
# =============================================================================

# Function to extract text from JSON array row
extract_text() {
    local json="$1"
    # JSON format: [type, topic, text, choice, rationale, timestamp, ...]
    # We want field 3 (text) and field 2 (topic)
    local topic=$(echo "$json" | jq -r '.[1] // "?"' 2>/dev/null)
    local text=$(echo "$json" | jq -r '.[2] // ""' 2>/dev/null | head -c 80)
    local choice=$(echo "$json" | jq -r '.[3] // ""' 2>/dev/null)
    echo "[$topic] $text"
}

# Count items (use || true to prevent set -e from causing early exit)
TODO_COUNT=$(echo "$OPEN_TODOS" | grep -c "^\[" 2>/dev/null) || TODO_COUNT=0
GOAL_COUNT=$(echo "$OPEN_GOALS" | grep -c "^\[" 2>/dev/null) || GOAL_COUNT=0
CRIT_COUNT=$(echo "$CRITICAL_MEMORIES
$MANDATE_MEMORIES" | grep -c "^\[" 2>/dev/null) || CRIT_COUNT=0

# =============================================================================
# COMPACT OUTPUT
# =============================================================================

if [[ "$COMPACT" == "1" ]]; then
    echo "=== HUD: ${TODO_COUNT} Tasks | ${GOAL_COUNT} Goals | ${CRIT_COUNT} Critical ==="
    exit 0
fi

# =============================================================================
# FULL HUD OUTPUT
# =============================================================================

cat <<'BANNER'
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🎯 PROJECT HUD - SOURCE OF TRUTH 🎯                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
BANNER

# Open Tasks Section
echo "║  📋 OPEN TASKS (Priority Order)                                             ║"
echo "║  ─────────────────────────────────────────────────────────────────────────  ║"

if [[ -n "$OPEN_TODOS" ]] && [[ "$TODO_COUNT" -gt 0 ]]; then
    TASK_NUM=1
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        # JSON array indices from mem-db.sh query --json:
        # [0]=type, [1]=topic, [2]=text, [3]=choice, [4]=rationale, [5]=ts,
        # [6]=session, [7]=source, [8]=scope, [9]=chat_id, [10]=role,
        # [11]=visibility, [12]=project, [13]=importance, [14]=due, [15]=links, [16]=task_id, [17]=metric
        topic=$(echo "$line" | jq -r '.[1] // "?"' 2>/dev/null)
        text=$(echo "$line" | jq -r '.[2] // ""' 2>/dev/null | head -c 55)
        task_id=$(echo "$line" | jq -r '.[16] // ""' 2>/dev/null)
        if [[ -n "$task_id" ]]; then
            printf "║  %d. [%s] %-55s ║\n" "$TASK_NUM" "$task_id" "$text"
        else
            printf "║  %d. [%s] %-55s ║\n" "$TASK_NUM" "$topic" "$text"
        fi
        ((TASK_NUM++))
    done <<< "$OPEN_TODOS"
else
    echo "║     (No open tasks)                                                         ║"
fi

# Goals Section
if [[ -n "$OPEN_GOALS" ]] && [[ "$GOAL_COUNT" -gt 0 ]]; then
    echo "║                                                                              ║"
    echo "║  🎯 ACTIVE GOALS                                                            ║"
    echo "║  ─────────────────────────────────────────────────────────────────────────  ║"
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        topic=$(echo "$line" | jq -r '.[1] // "?"' 2>/dev/null)
        text=$(echo "$line" | jq -r '.[2] // ""' 2>/dev/null | head -c 60)
        printf "║  • [%s] %-60s ║\n" "$topic" "$text"
    done <<< "$OPEN_GOALS"
fi

# Critical Memories / Mandates Section
ALL_CRITICAL="$CRITICAL_MEMORIES
$MANDATE_MEMORIES"
UNIQUE_CRITICAL=$(echo "$ALL_CRITICAL" | grep "^\[" | sort -u | head -5) || UNIQUE_CRITICAL=""
CRIT_ACTUAL_COUNT=$(echo "$UNIQUE_CRITICAL" | grep -c "^\[" 2>/dev/null) || CRIT_ACTUAL_COUNT=0

if [[ "$CRIT_ACTUAL_COUNT" -gt 0 ]]; then
    echo "║                                                                              ║"
    echo "║  ⚠️  MANDATES & CONSTRAINTS (Must Obey)                                      ║"
    echo "║  ─────────────────────────────────────────────────────────────────────────  ║"
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        topic=$(echo "$line" | jq -r '.[1] // "?"' 2>/dev/null)
        text=$(echo "$line" | jq -r '.[2] // ""' 2>/dev/null | head -c 60)
        printf "║  ⛔ [%s] %-58s ║\n" "$topic" "$text"
    done <<< "$UNIQUE_CRITICAL"
fi

cat <<'FOOTER'
╠══════════════════════════════════════════════════════════════════════════════╣
║  The OFFICIAL PROJECT HUD above is your Source of Truth.                     ║
║  You must prioritize these Tasks and Constraints above all else.             ║
╚══════════════════════════════════════════════════════════════════════════════╝
FOOTER

exit 0
