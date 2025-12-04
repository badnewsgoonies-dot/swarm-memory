#!/usr/bin/env bash
#
# manager-propose-todos.sh - Analyze eval results and propose next TODOs
#
# Usage:
#   ./manager-propose-todos.sh <task_id> [--create] [--topic <topic>]
#
# Example:
#   ./manager-propose-todos.sh clockcrypts-demo-001
#   ./manager-propose-todos.sh clockcrypts-demo-001 --create
#   ./manager-propose-todos.sh clockcrypts-demo-001 --create --topic clockcrypts-demo
#
# This script:
#   1. Queries all RESULT and LESSON glyphs for the task
#   2. Reads the game manual (docs/game_manual_demo.md)
#   3. Uses an LLM to analyze and propose 3 concrete next TODOs
#   4. Optionally creates the TODOs in memory (with --create)
#
# The manager acts as a "planning layer" that reads evaluations and
# produces actionable work items for the next orchestration round.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

TASK_ID=""
CREATE_TODOS=false
TOPIC="clockcrypts-demo"
MANUAL_PATH="docs/game_manual_demo.md"
LLM_TIER="${LLM_TIER:-smart}"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --create) CREATE_TODOS=true; shift ;;
    --topic) TOPIC="$2"; shift 2 ;;
    --manual) MANUAL_PATH="$2"; shift 2 ;;
    --tier) LLM_TIER="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 <task_id> [--create] [--topic <topic>] [--manual <path>] [--tier <tier>]"
      echo ""
      echo "Options:"
      echo "  --create       Actually create the TODOs in memory"
      echo "  --topic        Topic for new TODOs (default: clockcrypts-demo)"
      echo "  --manual       Path to game manual (default: docs/game_manual_demo.md)"
      echo "  --tier         LLM tier: fast, code, smart (default: smart)"
      exit 0
      ;;
    *)
      if [[ -z "$TASK_ID" ]]; then
        TASK_ID="$1"
      fi
      shift
      ;;
  esac
done

if [[ -z "$TASK_ID" ]]; then
  echo -e "${RED}Usage: $0 <task_id> [--create] [--topic <topic>]${NC}" >&2
  echo ""
  echo "Example:"
  echo "  $0 clockcrypts-demo-001"
  echo "  $0 clockcrypts-demo-001 --create"
  exit 1
fi

echo -e "${CYAN}${BOLD}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║           MANAGER: ANALYZE & PROPOSE NEXT TODOs            ║${NC}"
echo -e "${CYAN}${BOLD}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Task ID: ${BOLD}$TASK_ID${NC}"
echo -e "Topic:   ${BOLD}$TOPIC${NC}"
echo -e "Create:  ${BOLD}$CREATE_TODOS${NC}"
echo ""

# Gather context
echo -e "${MAGENTA}${BOLD}=== Gathering Context ===${NC}"
echo ""

# 1. Get RESULT glyphs
echo -e "${CYAN}Querying RESULT glyphs...${NC}"
results=$(./mem-db.sh query t=R task_id="$TASK_ID" limit=20 2>/dev/null || echo "No results found")
echo "$results" | head -10
echo ""

# 2. Get LESSON glyphs
echo -e "${CYAN}Querying LESSON glyphs...${NC}"
lessons=$(./mem-db.sh query t=L task_id="$TASK_ID" limit=20 2>/dev/null || echo "No lessons found")
echo "$lessons" | head -10
echo ""

# 3. Get PHASE glyphs
echo -e "${CYAN}Querying PHASE glyphs...${NC}"
phases=$(./mem-db.sh query t=P task_id="$TASK_ID" limit=10 2>/dev/null || echo "No phases found")
echo "$phases" | head -5
echo ""

# 4. Get current TODO status
echo -e "${CYAN}Querying current TODO status...${NC}"
todo_status=$(./mem-todo.sh list 2>/dev/null | grep -E "$TASK_ID|$TOPIC" | head -5 || echo "No matching TODOs")
echo "$todo_status"
echo ""

# 5. Read game manual (Definition of Demo Complete section)
echo -e "${CYAN}Reading game manual...${NC}"
manual_content=""
if [[ -f "$MANUAL_PATH" ]]; then
  # Extract the Definition of Demo Complete section
  manual_content=$(sed -n '/## 10. Definition of Demo Complete/,/## 11/p' "$MANUAL_PATH" 2>/dev/null | head -60)
  if [[ -z "$manual_content" ]]; then
    # Fallback: read last 100 lines which likely has the checklist
    manual_content=$(tail -100 "$MANUAL_PATH")
  fi
  echo "  Found manual: $MANUAL_PATH"
else
  echo -e "${YELLOW}  Manual not found at $MANUAL_PATH${NC}"
fi
echo ""

# Build LLM prompt
echo -e "${MAGENTA}${BOLD}=== Analyzing with LLM ===${NC}"
echo ""

prompt="You are a project manager analyzing evaluation results for a game development task.

TASK ID: $TASK_ID
TOPIC: $TOPIC

=== EVALUATION RESULTS (from demo-eval and human-playtest) ===
$results

=== LESSONS LEARNED ===
$lessons

=== PHASE HISTORY ===
$phases

=== CURRENT TODO STATUS ===
$todo_status

=== GAME MANUAL: DEFINITION OF DEMO COMPLETE ===
$manual_content

=== YOUR TASK ===

Based on the evaluation results, lessons learned, and the game manual's 'Definition of Demo Complete' checklist:

1. Identify what's WORKING (passed criteria)
2. Identify what's MISSING or BROKEN (failed criteria)
3. Propose exactly 3 concrete, actionable TODOs for the next iteration

Each TODO should be:
- Specific and actionable (not vague like 'improve things')
- Focused on one clear deliverable
- Aligned with the manual's Demo Complete criteria
- Ordered by priority (most critical first)

Output format (IMPORTANT - use this exact format):
---
ANALYSIS:
Working: [list what's working]
Missing: [list what's missing]
Priority: [what to fix first and why]

TODO 1:
ID: ${TASK_ID}-next-001
TEXT: [specific actionable task]
IMPORTANCE: H|M|L

TODO 2:
ID: ${TASK_ID}-next-002
TEXT: [specific actionable task]
IMPORTANCE: H|M|L

TODO 3:
ID: ${TASK_ID}-next-003
TEXT: [specific actionable task]
IMPORTANCE: H|M|L
---

Be specific! Instead of 'Add enemies', say 'Implement BasicClockwork enemy with chase behavior per manual section 4.1'."

# Call LLM
echo -e "${CYAN}Calling LLM (tier: $LLM_TIER)...${NC}"
echo ""

# Try hybrid LLM first, fallback to claude CLI
llm_response=""
if python3 -c "from llm_client import LLMClient" 2>/dev/null; then
  llm_response=$(python3 -c "
from llm_client import LLMClient
import sys
client = LLMClient()
prompt = '''$prompt'''
result = client.complete(prompt, tier='$LLM_TIER')
if result.success:
    print(result.text)
else:
    print(f'ERROR: {result.error}', file=sys.stderr)
    sys.exit(1)
" 2>&1)
else
  # Fallback to claude CLI
  llm_response=$(claude -p "$prompt" 2>&1 || echo "ERROR: LLM call failed")
fi

if [[ "$llm_response" == ERROR:* ]]; then
  echo -e "${RED}$llm_response${NC}"
  exit 1
fi

echo -e "${GREEN}${BOLD}=== LLM ANALYSIS ===${NC}"
echo ""
echo "$llm_response"
echo ""

# Parse TODOs from response
echo -e "${MAGENTA}${BOLD}=== PROPOSED TODOs ===${NC}"
echo ""

# Extract TODO blocks
todo1_id=$(echo "$llm_response" | grep -A1 "TODO 1:" | grep "ID:" | sed 's/.*ID: *//' | tr -d ' ')
todo1_text=$(echo "$llm_response" | grep -A2 "TODO 1:" | grep "TEXT:" | sed 's/.*TEXT: *//')
todo1_imp=$(echo "$llm_response" | grep -A3 "TODO 1:" | grep "IMPORTANCE:" | sed 's/.*IMPORTANCE: *//' | tr -d ' ')

todo2_id=$(echo "$llm_response" | grep -A1 "TODO 2:" | grep "ID:" | sed 's/.*ID: *//' | tr -d ' ')
todo2_text=$(echo "$llm_response" | grep -A2 "TODO 2:" | grep "TEXT:" | sed 's/.*TEXT: *//')
todo2_imp=$(echo "$llm_response" | grep -A3 "TODO 2:" | grep "IMPORTANCE:" | sed 's/.*IMPORTANCE: *//' | tr -d ' ')

todo3_id=$(echo "$llm_response" | grep -A1 "TODO 3:" | grep "ID:" | sed 's/.*ID: *//' | tr -d ' ')
todo3_text=$(echo "$llm_response" | grep -A2 "TODO 3:" | grep "TEXT:" | sed 's/.*TEXT: *//')
todo3_imp=$(echo "$llm_response" | grep -A3 "TODO 3:" | grep "IMPORTANCE:" | sed 's/.*IMPORTANCE: *//' | tr -d ' ')

# Display parsed TODOs
echo -e "${CYAN}TODO 1:${NC}"
echo "  ID: $todo1_id"
echo "  Text: $todo1_text"
echo "  Importance: $todo1_imp"
echo ""

echo -e "${CYAN}TODO 2:${NC}"
echo "  ID: $todo2_id"
echo "  Text: $todo2_text"
echo "  Importance: $todo2_imp"
echo ""

echo -e "${CYAN}TODO 3:${NC}"
echo "  ID: $todo3_id"
echo "  Text: $todo3_text"
echo "  Importance: $todo3_imp"
echo ""

# Create TODOs if requested
if [[ "$CREATE_TODOS" == true ]]; then
  echo -e "${GREEN}${BOLD}=== CREATING TODOs ===${NC}"
  echo ""

  created=0

  if [[ -n "$todo1_id" && -n "$todo1_text" ]]; then
    ./mem-todo.sh add --id "$todo1_id" --topic "$TOPIC" --text "$todo1_text" --importance "${todo1_imp:-H}" 2>/dev/null && {
      echo -e "${GREEN}Created: $todo1_id${NC}"
      ((created++))
    } || echo -e "${YELLOW}Failed to create: $todo1_id${NC}"
  fi

  if [[ -n "$todo2_id" && -n "$todo2_text" ]]; then
    ./mem-todo.sh add --id "$todo2_id" --topic "$TOPIC" --text "$todo2_text" --importance "${todo2_imp:-M}" 2>/dev/null && {
      echo -e "${GREEN}Created: $todo2_id${NC}"
      ((created++))
    } || echo -e "${YELLOW}Failed to create: $todo2_id${NC}"
  fi

  if [[ -n "$todo3_id" && -n "$todo3_text" ]]; then
    ./mem-todo.sh add --id "$todo3_id" --topic "$TOPIC" --text "$todo3_text" --importance "${todo3_imp:-M}" 2>/dev/null && {
      echo -e "${GREEN}Created: $todo3_id${NC}"
      ((created++))
    } || echo -e "${YELLOW}Failed to create: $todo3_id${NC}"
  fi

  echo ""
  echo -e "${GREEN}Created $created TODO(s)${NC}"

  # Log the manager action
  ./mem-log.sh lesson \
    --task "$TASK_ID" \
    --topic "$TOPIC" \
    --source "manager" \
    --text "Manager analyzed evals and created $created follow-up TODOs: ${todo1_id:-?}, ${todo2_id:-?}, ${todo3_id:-?}. Based on RESULT/LESSON glyphs from demo-eval and human-playtest."

  echo ""
  echo "View new TODOs:"
  echo -e "  ${CYAN}./mem-todo.sh list --topic $TOPIC${NC}"
else
  echo -e "${YELLOW}TODOs not created. Run with --create to add them to memory.${NC}"
  echo ""
  echo "To create these TODOs:"
  echo -e "  ${CYAN}$0 $TASK_ID --create${NC}"
fi

echo ""
echo "Next steps:"
echo "  1. Review proposed TODOs above"
echo "  2. Run with --create to add them to memory"
echo "  3. Run orchestrator on the new TODOs"
echo "  4. Repeat eval cycle"
