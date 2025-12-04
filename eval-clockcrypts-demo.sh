#!/usr/bin/env bash
#
# eval-clockcrypts-demo.sh - Structural + build evaluation for Clockwork Crypts demo
#
# Usage:
#   ./eval-clockcrypts-demo.sh <task_id> <repo-root> [manual-path]
#
# Example:
#   ./eval-clockcrypts-demo.sh clockcrypts-demo-001 /home/geni/clockcrypts-godot docs/game_manual_demo.md
#
# This script:
#   1. Checks that the game manual exists
#   2. Verifies project structure (project.godot, entry scene, player script)
#   3. Attempts a headless build/run via godot4 CLI
#   4. Logs a RESULT glyph with metrics (demo_complete, build_ok, missing_count)
#   5. Logs a LESSON glyph with recommendations for next iteration
#
# Requirements:
#   - Godot 4 CLI (godot4) in PATH for build checks
#   - mem-log.sh in the same directory
#
# The eval does NOT verify gameplay feel or manual compliance.
# That requires human playtest (see human-playtest.sh).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

TASK_ID="${1:-}"
REPO_ROOT="${2:-}"
MANUAL_PATH="${3:-docs/game_manual_demo.md}"

if [[ -z "$TASK_ID" || -z "$REPO_ROOT" ]]; then
  echo -e "${RED}Usage: $0 <task_id> <repo-root> [manual-path]${NC}" >&2
  echo ""
  echo "Example:"
  echo "  $0 clockcrypts-demo-001 /home/geni/clockcrypts-godot"
  exit 1
fi

if [[ ! -d "$REPO_ROOT" ]]; then
  echo -e "${RED}ERROR: repo root not found: $REPO_ROOT${NC}" >&2
  exit 1
fi

REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"  # Resolve absolute path

echo -e "${CYAN}${BOLD}== Clockcrypts Demo Eval ==${NC}"
echo "Task ID    : $TASK_ID"
echo "Repo Root  : $REPO_ROOT"
echo "Manual     : $MANUAL_PATH"
echo ""

cd "$REPO_ROOT"

missing=()
found=()

# 1) Manual exists
if [[ ! -f "$MANUAL_PATH" ]]; then
  echo -e "${RED}!! Missing manual: $MANUAL_PATH${NC}"
  missing+=("manual:$MANUAL_PATH")
else
  echo -e "${GREEN}✓ Found manual: $MANUAL_PATH${NC}"
  found+=("manual")
fi

# 2) Godot project file
if [[ ! -f "project.godot" ]]; then
  echo -e "${RED}!! Missing project.godot${NC}"
  missing+=("project.godot")
else
  echo -e "${GREEN}✓ Found project.godot${NC}"
  found+=("project.godot")
fi

# 3) Entry scene (check multiple common patterns)
entry_scene=""
for scene in "scenes/Main.tscn" "scenes/MainScene.tscn" "Main.tscn" "scenes/TitleScreen.tscn" "scenes/Game.tscn"; do
  if [[ -f "$scene" ]]; then
    entry_scene="$scene"
    break
  fi
done

if [[ -z "$entry_scene" ]]; then
  echo -e "${RED}!! Missing entry scene (scenes/Main.tscn or similar)${NC}"
  missing+=("entry_scene")
else
  echo -e "${GREEN}✓ Found entry scene: $entry_scene${NC}"
  found+=("entry_scene:$entry_scene")
fi

# 4) Player script (C# or GDScript)
player_script=""
for script in "scripts/Player.cs" "scripts/Player.gd" "Scripts/Player.cs" "src/Player.cs" "player/Player.cs" "player/Player.gd"; do
  if [[ -f "$script" ]]; then
    player_script="$script"
    break
  fi
done

if [[ -z "$player_script" ]]; then
  echo -e "${RED}!! Missing player script (scripts/Player.cs or scripts/Player.gd)${NC}"
  missing+=("player_script")
else
  echo -e "${GREEN}✓ Found player script: $player_script${NC}"
  found+=("player_script:$player_script")
fi

# 5) Enemy scripts (at least one)
enemy_script=""
for script in "scripts/Enemy.cs" "scripts/Enemy.gd" "scripts/BasicEnemy.cs" "scripts/Turret.cs" "enemies/Enemy.cs"; do
  if [[ -f "$script" ]]; then
    enemy_script="$script"
    break
  fi
done

if [[ -z "$enemy_script" ]]; then
  echo -e "${YELLOW}?? No enemy script found (optional check)${NC}"
else
  echo -e "${GREEN}✓ Found enemy script: $enemy_script${NC}"
  found+=("enemy_script:$enemy_script")
fi

# 6) Boss script
boss_script=""
for script in "scripts/Boss.cs" "scripts/GearGolem.cs" "scripts/Boss.gd" "enemies/Boss.cs"; do
  if [[ -f "$script" ]]; then
    boss_script="$script"
    break
  fi
done

if [[ -z "$boss_script" ]]; then
  echo -e "${YELLOW}?? No boss script found (optional check)${NC}"
else
  echo -e "${GREEN}✓ Found boss script: $boss_script${NC}"
  found+=("boss_script:$boss_script")
fi

# 7) Room scenes (count)
room_count=0
for room in scenes/Room*.tscn scenes/room*.tscn rooms/*.tscn levels/*.tscn; do
  if [[ -f "$room" ]]; then
    ((room_count++)) || true
  fi
done

if [[ "$room_count" -lt 5 ]]; then
  echo -e "${YELLOW}?? Found $room_count room scenes (demo needs 5)${NC}"
else
  echo -e "${GREEN}✓ Found $room_count room scenes${NC}"
  found+=("rooms:$room_count")
fi

echo ""

# 8) Build / headless check
build_ok=false
build_log="/tmp/clockcrypts-build-$(date +%Y%m%d-%H%M%S).log"

if command -v godot4 >/dev/null 2>&1; then
  echo -e "${CYAN}Running: godot4 --headless --path \"$REPO_ROOT\" --quit${NC}"
  if godot4 --headless --path "$REPO_ROOT" --quit >"$build_log" 2>&1; then
    echo -e "${GREEN}✓ Godot headless run succeeded${NC}"
    build_ok=true
  else
    echo -e "${RED}!! Godot headless run FAILED${NC}"
    echo "   See log: $build_log"
    # Show last 10 lines of build log
    echo -e "${YELLOW}Last 10 lines of build log:${NC}"
    tail -10 "$build_log" | sed 's/^/   /'
    build_ok=false
  fi
elif command -v godot >/dev/null 2>&1; then
  echo -e "${CYAN}Running: godot --headless --path \"$REPO_ROOT\" --quit${NC}"
  if godot --headless --path "$REPO_ROOT" --quit >"$build_log" 2>&1; then
    echo -e "${GREEN}✓ Godot headless run succeeded${NC}"
    build_ok=true
  else
    echo -e "${RED}!! Godot headless run FAILED${NC}"
    echo "   See log: $build_log"
    build_ok=false
  fi
else
  echo -e "${YELLOW}!! godot4/godot CLI not found in PATH - skipping build check${NC}"
  echo "   Install Godot 4 CLI or add to PATH for build verification"
fi

echo ""

# Calculate results
missing_count="${#missing[@]}"
found_count="${#found[@]}"
demo_complete=false
reason=""

if [[ "$missing_count" -gt 0 ]]; then
  reason+="Missing pieces: ${missing[*]}. "
fi
if [[ "$build_ok" != true ]]; then
  reason+="Build/headless run did not complete cleanly. "
fi

if [[ "$missing_count" -eq 0 && "$build_ok" == true ]]; then
  demo_complete=true
  reason="Structural + build checks passed. Still requires human playtest against the manual's 'Demo Complete' section."
fi

echo -e "${CYAN}${BOLD}== Eval Summary ==${NC}"
echo "demo_complete = $demo_complete"
echo "build_ok      = $build_ok"
echo "missing_count = $missing_count"
echo "found_count   = $found_count"
echo "room_count    = $room_count"
echo "reason        = ${reason:-no issues found}"
echo ""

# 9) Log into memory DB as RESULT + LESSON
cd "$SCRIPT_DIR"

# Build metric string
metric="demo_complete=${demo_complete};build_ok=${build_ok};missing_count=${missing_count};found_count=${found_count};room_count=${room_count}"

# Safely join missing array
missing_str=""
if [[ ${#missing[@]} -gt 0 ]]; then
  missing_str="${missing[*]}"
else
  missing_str="none"
fi

./mem-log.sh result \
  --task "$TASK_ID" \
  --success "$demo_complete" \
  --topic "clockcrypts-demo" \
  --source "demo-eval" \
  --metric "$metric" \
  --text "Clockcrypts demo structural eval: ${reason:-no issues found} (missing=${missing_str})."

# Generate lesson based on what's missing
lesson_text="Demo eval: demo_complete=${demo_complete}, build_ok=${build_ok}. "
if [[ "$demo_complete" == true ]]; then
  lesson_text+="Ready for human playtest. Run human-playtest.sh after testing gameplay."
elif [[ "$missing_count" -gt 0 ]]; then
  lesson_text+="Next orchestration round should focus on: ${missing_str}. "
  lesson_text+="Align with the manual's 'Demo Complete' checklist."
else
  lesson_text+="Build issues need resolution before demo can be tested."
fi

./mem-log.sh lesson \
  --task "$TASK_ID" \
  --topic "clockcrypts-demo" \
  --source "demo-eval" \
  --text "$lesson_text"

echo -e "${GREEN}Logged RESULT and LESSON for task: $TASK_ID${NC}"
echo ""
echo "Next steps:"
if [[ "$demo_complete" == true ]]; then
  echo "  1. Play the game and verify against docs/game_manual_demo.md"
  echo "  2. Run: ./human-playtest.sh $TASK_ID"
else
  echo "  1. Fix missing items: ${missing_str}"
  echo "  2. Re-run: $0 $TASK_ID $REPO_ROOT"
fi
