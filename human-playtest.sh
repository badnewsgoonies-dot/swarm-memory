#!/usr/bin/env bash
#
# human-playtest.sh - Interactive helper for logging human playtest results
#
# Usage:
#   ./human-playtest.sh <task_id> [--quick]
#
# Example:
#   ./human-playtest.sh clockcrypts-demo-001
#   ./human-playtest.sh clockcrypts-demo-001 --quick  # Skip prompts, use args
#
# This script walks you through a structured playtest checklist and logs:
#   - A RESULT glyph with pass/fail and metrics
#   - A LESSON glyph with observations for future iterations
#
# The eval script (eval-clockcrypts-demo.sh) checks structure + build.
# This script checks gameplay feel and manual compliance.
#
# Checklist covers:
#   1. Core Flow (title, start run, rooms, boss, summary)
#   2. Player (movement, attack, health, death)
#   3. Enemies (spawn, behaviors, variety)
#   4. Rooms (layouts, doors, progression)
#   5. Boss (encounter, phases)
#   6. Polish (UI, no crashes, no soft-locks)

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

TASK_ID="${1:-}"
QUICK_MODE="${2:-}"

if [[ -z "$TASK_ID" ]]; then
  echo -e "${RED}Usage: $0 <task_id> [--quick]${NC}" >&2
  echo ""
  echo "Example:"
  echo "  $0 clockcrypts-demo-001"
  exit 1
fi

echo -e "${CYAN}${BOLD}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║       CLOCKWORK CRYPTS - HUMAN PLAYTEST CHECKLIST          ║${NC}"
echo -e "${CYAN}${BOLD}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Task ID: ${BOLD}$TASK_ID${NC}"
echo ""
echo -e "${YELLOW}Play the game first, then answer the following questions.${NC}"
echo -e "${YELLOW}Reference: docs/game_manual_demo.md - 'Definition of Demo Complete'${NC}"
echo ""

# Initialize metrics
loop_complete=false
title_works=false
player_works=false
enemies_work=false
rooms_work=false
boss_seen=false
summary_screen=false
unique_rooms=0
enemy_types=0
crashes=0

# Helper function for yes/no questions
ask_yn() {
  local prompt="$1"
  local default="${2:-n}"
  local answer=""

  if [[ "$QUICK_MODE" == "--quick" ]]; then
    echo -e "$prompt ${YELLOW}[skipped in quick mode, default: $default]${NC}"
    answer="$default"
  else
    while true; do
      read -p "$prompt (y/n) [$default]: " answer
      answer="${answer:-$default}"
      case "$answer" in
        [Yy]* ) answer="y"; break;;
        [Nn]* ) answer="n"; break;;
        * ) echo "Please answer y or n.";;
      esac
    done
  fi

  [[ "$answer" == "y" ]]
}

# Helper function for numeric questions
ask_num() {
  local prompt="$1"
  local default="${2:-0}"
  local answer=""

  if [[ "$QUICK_MODE" == "--quick" ]]; then
    echo -e "$prompt ${YELLOW}[skipped in quick mode, default: $default]${NC}"
    answer="$default"
  else
    read -p "$prompt [$default]: " answer
    answer="${answer:-$default}"
  fi

  echo "$answer"
}

echo -e "${MAGENTA}${BOLD}=== 1. CORE FLOW ===${NC}"
echo ""

if ask_yn "Can you see the title screen and start a new run?"; then
  title_works=true
  echo -e "  ${GREEN}✓ Title screen works${NC}"
else
  echo -e "  ${RED}✗ Title screen issue${NC}"
fi

if ask_yn "Can you reach the summary screen (after death or victory)?"; then
  summary_screen=true
  echo -e "  ${GREEN}✓ Summary screen works${NC}"
else
  echo -e "  ${RED}✗ No summary screen${NC}"
fi

if ask_yn "Can you return to title from summary screen?"; then
  loop_complete=true
  echo -e "  ${GREEN}✓ Full loop complete${NC}"
else
  echo -e "  ${RED}✗ Loop not complete${NC}"
fi

echo ""
echo -e "${MAGENTA}${BOLD}=== 2. PLAYER ===${NC}"
echo ""

if ask_yn "Does player movement work (WASD/arrows)?"; then
  player_works=true
  echo -e "  ${GREEN}✓ Movement works${NC}"
else
  echo -e "  ${RED}✗ Movement broken${NC}"
fi

if ask_yn "Does player attack work (shoot/melee)?"; then
  echo -e "  ${GREEN}✓ Attack works${NC}"
else
  player_works=false
  echo -e "  ${RED}✗ Attack broken${NC}"
fi

if ask_yn "Does player health/damage work (can die)?"; then
  echo -e "  ${GREEN}✓ Health system works${NC}"
else
  player_works=false
  echo -e "  ${RED}✗ Health system broken${NC}"
fi

echo ""
echo -e "${MAGENTA}${BOLD}=== 3. ENEMIES ===${NC}"
echo ""

enemy_types=$(ask_num "How many distinct enemy types did you see?" "0")

if [[ "$enemy_types" -gt 0 ]]; then
  enemies_work=true
  echo -e "  ${GREEN}✓ Found $enemy_types enemy type(s)${NC}"
else
  echo -e "  ${RED}✗ No enemies found${NC}"
fi

if ask_yn "Do enemies behave differently (chase, shoot, etc.)?"; then
  echo -e "  ${GREEN}✓ Enemy variety present${NC}"
else
  echo -e "  ${YELLOW}? Enemies lack variety${NC}"
fi

echo ""
echo -e "${MAGENTA}${BOLD}=== 4. ROOMS ===${NC}"
echo ""

unique_rooms=$(ask_num "How many unique room layouts did you see?" "0")

if [[ "$unique_rooms" -ge 3 ]]; then
  rooms_work=true
  echo -e "  ${GREEN}✓ Room variety OK ($unique_rooms rooms)${NC}"
else
  echo -e "  ${YELLOW}? Limited room variety ($unique_rooms rooms)${NC}"
fi

if ask_yn "Do doors/exits work for room progression?"; then
  echo -e "  ${GREEN}✓ Room progression works${NC}"
else
  rooms_work=false
  echo -e "  ${RED}✗ Room progression broken${NC}"
fi

echo ""
echo -e "${MAGENTA}${BOLD}=== 5. BOSS ===${NC}"
echo ""

if ask_yn "Did you encounter a boss/elite enemy?"; then
  boss_seen=true
  echo -e "  ${GREEN}✓ Boss encounter present${NC}"
else
  echo -e "  ${RED}✗ No boss encounter${NC}"
fi

if [[ "$boss_seen" == true ]]; then
  if ask_yn "Does the boss have distinct phases/behaviors?"; then
    echo -e "  ${GREEN}✓ Boss has phases${NC}"
  else
    echo -e "  ${YELLOW}? Boss lacks phases${NC}"
  fi
fi

echo ""
echo -e "${MAGENTA}${BOLD}=== 6. STABILITY ===${NC}"
echo ""

crashes=$(ask_num "How many crashes did you experience?" "0")

if [[ "$crashes" -eq 0 ]]; then
  echo -e "  ${GREEN}✓ No crashes${NC}"
else
  echo -e "  ${RED}✗ $crashes crash(es)${NC}"
fi

if ask_yn "Were there any soft-locks (stuck without being able to continue)?"; then
  echo -e "  ${RED}✗ Soft-locks present${NC}"
else
  echo -e "  ${GREEN}✓ No soft-locks${NC}"
fi

echo ""
echo -e "${MAGENTA}${BOLD}=== ADDITIONAL NOTES ===${NC}"
echo ""

notes=""
if [[ "$QUICK_MODE" != "--quick" ]]; then
  echo "Enter any additional observations (press Enter to skip):"
  read -p "> " notes
fi

echo ""

# Calculate overall success
demo_complete=false
if [[ "$title_works" == true && "$player_works" == true && "$loop_complete" == true ]]; then
  if [[ "$enemy_types" -ge 2 && "$unique_rooms" -ge 3 && "$boss_seen" == true && "$crashes" -eq 0 ]]; then
    demo_complete=true
  fi
fi

# Build summary
echo -e "${CYAN}${BOLD}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║                    PLAYTEST SUMMARY                        ║${NC}"
echo -e "${CYAN}${BOLD}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

if [[ "$demo_complete" == true ]]; then
  echo -e "${GREEN}${BOLD}DEMO COMPLETE: YES${NC}"
  echo -e "The demo meets the 'Definition of Demo Complete' criteria."
else
  echo -e "${RED}${BOLD}DEMO COMPLETE: NO${NC}"
  echo -e "Some criteria from 'Definition of Demo Complete' are not met."
fi

echo ""
echo "Metrics:"
echo "  loop_complete   = $loop_complete"
echo "  title_works     = $title_works"
echo "  player_works    = $player_works"
echo "  enemies_work    = $enemies_work"
echo "  rooms_work      = $rooms_work"
echo "  boss_seen       = $boss_seen"
echo "  summary_screen  = $summary_screen"
echo "  unique_rooms    = $unique_rooms"
echo "  enemy_types     = $enemy_types"
echo "  crashes         = $crashes"
echo ""

# Build improvement recommendations
improvements=()
[[ "$title_works" != true ]] && improvements+=("Fix title screen")
[[ "$player_works" != true ]] && improvements+=("Fix player mechanics")
[[ "$enemies_work" != true ]] && improvements+=("Add working enemies")
[[ "$rooms_work" != true ]] && improvements+=("Fix room progression")
[[ "$boss_seen" != true ]] && improvements+=("Add boss encounter")
[[ "$summary_screen" != true ]] && improvements+=("Add summary screen")
[[ "$loop_complete" != true ]] && improvements+=("Complete game loop")
[[ "$crashes" -gt 0 ]] && improvements+=("Fix crash issues")
[[ "$unique_rooms" -lt 5 ]] && improvements+=("Add more room variety (need 5)")
[[ "$enemy_types" -lt 3 ]] && improvements+=("Add more enemy types (need 3)")

# Log to memory
metric="demo_complete=${demo_complete};loop_complete=${loop_complete};boss_seen=${boss_seen};summary_screen=${summary_screen};unique_rooms=${unique_rooms};enemy_types=${enemy_types};crashes=${crashes}"

# Build result text
result_text="Human playtest: "
if [[ "$demo_complete" == true ]]; then
  result_text+="Demo meets manual criteria. "
else
  result_text+="Demo incomplete. "
fi
result_text+="Rooms=$unique_rooms, Enemies=$enemy_types, Crashes=$crashes."
[[ -n "$notes" ]] && result_text+=" Notes: $notes"

./mem-log.sh result \
  --task "$TASK_ID" \
  --success "$demo_complete" \
  --topic "clockcrypts-demo" \
  --source "human-playtest" \
  --metric "$metric" \
  --text "$result_text"

# Build lesson text
lesson_text="Human playtest evaluation: "
if [[ "$demo_complete" == true ]]; then
  lesson_text+="Demo is playable and complete per manual. "
  lesson_text+="Future work: polish, difficulty tuning, more content variety."
else
  if [[ ${#improvements[@]} -gt 0 ]]; then
    lesson_text+="Priority improvements: ${improvements[*]}. "
  fi
  lesson_text+="Align next iteration with 'Definition of Demo Complete' in manual."
fi

./mem-log.sh lesson \
  --task "$TASK_ID" \
  --topic "clockcrypts-demo" \
  --source "human-playtest" \
  --text "$lesson_text"

echo -e "${GREEN}Logged RESULT and LESSON for task: $TASK_ID${NC}"
echo ""

# Show what was logged
echo "To view all evaluations for this task:"
echo -e "  ${CYAN}./mem-log.sh history $TASK_ID${NC}"
echo ""

if [[ ${#improvements[@]} -gt 0 ]]; then
  echo -e "${YELLOW}Suggested improvements for next iteration:${NC}"
  for imp in "${improvements[@]}"; do
    echo "  - $imp"
  done
fi
