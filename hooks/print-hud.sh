#!/usr/bin/env bash
#
# print-hud.sh - Context Nexus HUD Generator
#
# Generates an ASCII HUD (Heads-Up Display) for the "Holy Triangle" architecture.
# The HUD shows:
#   1. Current Date/Time
#   2. Top 5 OPEN Tasks (from mem-db.sh query t=T)
#   3. Any "MANDATE" or "CRITICAL" memories (High importance Decisions)
#
# Usage:
#   ./print-hud.sh              # Output HUD to stdout
#   ./print-hud.sh --json       # Output as JSON for programmatic use
#
# The HUD acts as the "Source of Truth" for the agent's current context.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEM_DB="${SCRIPT_DIR}/../mem-db.sh"

JSON_OUTPUT=0
for arg in "$@"; do
    [[ "$arg" == "--json" ]] && JSON_OUTPUT=1
done

# Get current date/time
CURRENT_TIME=$(date '+%Y-%m-%d %H:%M:%S %Z')
CURRENT_DATE=$(date '+%A, %B %d, %Y')

# Query top 5 OPEN tasks
OPEN_TASKS=$("$MEM_DB" query t=T choice=OPEN limit=5 --json 2>/dev/null || echo "")

# Query MANDATE/CRITICAL memories (High importance decisions)
MANDATES=$("$MEM_DB" query t=d importance=H limit=5 --json 2>/dev/null || echo "")

# Also query high importance notes/facts as potential mandates
CRITICAL_FACTS=$("$MEM_DB" query t=f importance=H limit=3 --json 2>/dev/null || echo "")

if [[ $JSON_OUTPUT -eq 1 ]]; then
    # JSON output for programmatic use
    cat <<EOF
{
  "timestamp": "$CURRENT_TIME",
  "date": "$CURRENT_DATE",
  "open_tasks": [$(echo "$OPEN_TASKS" | tr '\n' ',' | sed 's/,$//')],
  "mandates": [$(echo "$MANDATES" | tr '\n' ',' | sed 's/,$//')],
  "critical_facts": [$(echo "$CRITICAL_FACTS" | tr '\n' ',' | sed 's/,$//')]
}
EOF
    exit 0
fi

# ASCII HUD output
cat <<'BANNER'
╔══════════════════════════════════════════════════════════════════════════════╗
║                           🎯 PROJECT HUD - CONTEXT NEXUS                     ║
╠══════════════════════════════════════════════════════════════════════════════╣
BANNER

printf "║  📅 %-72s ║\n" "$CURRENT_DATE"
printf "║  🕐 %-72s ║\n" "$CURRENT_TIME"

cat <<'BANNER'
╠══════════════════════════════════════════════════════════════════════════════╣
║                              📋 OPEN TASKS (Top 5)                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
BANNER

# Parse and display open tasks
if [[ -n "$OPEN_TASKS" ]]; then
    TASK_NUM=1
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        # Extract text (index 2) and topic (index 1) from JSON array
        TASK_TOPIC=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[1] or 'general')" 2>/dev/null || echo "?")
        TASK_TEXT=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d[2] or '')[:60])" 2>/dev/null || echo "?")
        printf "║  %d. [%-12s] %-56s ║\n" "$TASK_NUM" "$TASK_TOPIC" "$TASK_TEXT"
        ((TASK_NUM++))
    done <<< "$OPEN_TASKS"
else
    printf "║  %-76s ║\n" "(No open tasks)"
fi

cat <<'BANNER'
╠══════════════════════════════════════════════════════════════════════════════╣
║                         ⚠️  MANDATES (Critical Rules)                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
BANNER

# Parse and display mandates (high importance decisions)
HAS_MANDATES=0
if [[ -n "$MANDATES" ]]; then
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        HAS_MANDATES=1
        MANDATE_TOPIC=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[1] or 'policy')" 2>/dev/null || echo "?")
        MANDATE_TEXT=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d[2] or '')[:60])" 2>/dev/null || echo "?")
        MANDATE_CHOICE=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[3] or '')" 2>/dev/null || echo "")
        if [[ -n "$MANDATE_CHOICE" ]]; then
            printf "║  🔒 [%-10s] %-50s → %s ║\n" "$MANDATE_TOPIC" "$MANDATE_TEXT" "$MANDATE_CHOICE"
        else
            printf "║  🔒 [%-10s] %-60s ║\n" "$MANDATE_TOPIC" "$MANDATE_TEXT"
        fi
    done <<< "$MANDATES"
fi

if [[ -n "$CRITICAL_FACTS" ]]; then
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        HAS_MANDATES=1
        FACT_TOPIC=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[1] or 'fact')" 2>/dev/null || echo "?")
        FACT_TEXT=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d[2] or '')[:68])" 2>/dev/null || echo "?")
        printf "║  📌 [%-10s] %-60s ║\n" "$FACT_TOPIC" "$FACT_TEXT"
    done <<< "$CRITICAL_FACTS"
fi

if [[ $HAS_MANDATES -eq 0 ]]; then
    printf "║  %-76s ║\n" "(No critical mandates)"
fi

cat <<'BANNER'
╚══════════════════════════════════════════════════════════════════════════════╝
BANNER

# Print instruction footer
echo ""
echo "This HUD is the SOURCE OF TRUTH. Respect all MANDATEs as constraints."
echo ""
