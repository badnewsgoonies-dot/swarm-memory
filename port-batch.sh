#!/bin/bash
# Vale Village v1->v2 Porting Batch Script
# Phase 1: Direct copy pure TS (fast)
# Phase 2+: Queue daemon tasks for React->Preact conversion

set -e

V1="/home/geni/Documents/vale-village"
V2="/home/geni/Documents/vale-village-v2"
DAEMON="./swarm_daemon.py"
QUEUE_FILE="/home/geni/swarm/memory/port-queue.txt"

echo "=== Vale Village Port: Phase 1 - Direct Copy ==="

# Create directory structure
mkdir -p "$V2/src/core/algorithms"
mkdir -p "$V2/src/core/config"
mkdir -p "$V2/src/core/migrations"
mkdir -p "$V2/src/core/models"
mkdir -p "$V2/src/core/random"
mkdir -p "$V2/src/core/save"
mkdir -p "$V2/src/core/services"
mkdir -p "$V2/src/core/utils"
mkdir -p "$V2/src/core/validation"
mkdir -p "$V2/src/data/definitions"
mkdir -p "$V2/src/data/schemas"
mkdir -p "$V2/src/data/types"
mkdir -p "$V2/src/story"
mkdir -p "$V2/src/utils"
mkdir -p "$V2/src/infra/save"
mkdir -p "$V2/src/ui/state"
mkdir -p "$V2/src/ui/components/battle"
mkdir -p "$V2/src/ui/components/storyboards"
mkdir -p "$V2/src/ui/sprites/mappings"
mkdir -p "$V2/src/ui/hooks"
mkdir -p "$V2/src/ui/utils"

# Copy pure TypeScript files (no React dependencies)
echo "Copying core/algorithms..."
cp -r "$V1/src/core/algorithms/"* "$V2/src/core/algorithms/" 2>/dev/null || true

echo "Copying core/config..."
cp -r "$V1/src/core/config/"* "$V2/src/core/config/" 2>/dev/null || true

echo "Copying core/constants.ts..."
cp "$V1/src/core/constants.ts" "$V2/src/core/" 2>/dev/null || true

echo "Copying core/migrations..."
cp -r "$V1/src/core/migrations/"* "$V2/src/core/migrations/" 2>/dev/null || true

echo "Copying core/models..."
cp -r "$V1/src/core/models/"* "$V2/src/core/models/" 2>/dev/null || true

echo "Copying core/random..."
cp -r "$V1/src/core/random/"* "$V2/src/core/random/" 2>/dev/null || true

echo "Copying core/save..."
cp -r "$V1/src/core/save/"* "$V2/src/core/save/" 2>/dev/null || true

echo "Copying core/services..."
cp -r "$V1/src/core/services/"* "$V2/src/core/services/" 2>/dev/null || true

echo "Copying core/utils..."
cp -r "$V1/src/core/utils/"* "$V2/src/core/utils/" 2>/dev/null || true

echo "Copying core/validation..."
cp -r "$V1/src/core/validation/"* "$V2/src/core/validation/" 2>/dev/null || true

echo "Copying data/definitions..."
cp -r "$V1/src/data/definitions/"* "$V2/src/data/definitions/" 2>/dev/null || true

echo "Copying data/schemas..."
cp -r "$V1/src/data/schemas/"* "$V2/src/data/schemas/" 2>/dev/null || true

echo "Copying data/types..."
cp -r "$V1/src/data/types/"* "$V2/src/data/types/" 2>/dev/null || true

echo "Copying story..."
cp -r "$V1/src/story/"* "$V2/src/story/" 2>/dev/null || true

echo "Copying utils..."
cp -r "$V1/src/utils/"* "$V2/src/utils/" 2>/dev/null || true

echo "Copying infra..."
cp -r "$V1/src/infra/save/"* "$V2/src/infra/save/" 2>/dev/null || true

# Copy pure TS sprite files
echo "Copying sprite types and mappings..."
cp "$V1/src/ui/sprites/types.ts" "$V2/src/ui/sprites/" 2>/dev/null || true
cp "$V1/src/ui/sprites/catalog.ts" "$V2/src/ui/sprites/" 2>/dev/null || true
cp "$V1/src/ui/sprites/manifest.ts" "$V2/src/ui/sprites/" 2>/dev/null || true
cp "$V1/src/ui/sprites/loader.ts" "$V2/src/ui/sprites/" 2>/dev/null || true
cp "$V1/src/ui/sprites/utils.ts" "$V2/src/ui/sprites/" 2>/dev/null || true
cp "$V1/src/ui/sprites/sprite-list-generated.ts" "$V2/src/ui/sprites/" 2>/dev/null || true
cp -r "$V1/src/ui/sprites/mappings/"* "$V2/src/ui/sprites/mappings/" 2>/dev/null || true

# Copy state files (Zustand works same with Preact)
echo "Copying Zustand state slices..."
cp "$V1/src/ui/state/"*.ts "$V2/src/ui/state/" 2>/dev/null || true

# Copy hooks
echo "Copying hooks..."
cp "$V1/src/ui/hooks/"*.ts "$V2/src/ui/hooks/" 2>/dev/null || true

# Copy ui utils
echo "Copying ui utils..."
cp "$V1/src/ui/utils/"*.ts "$V2/src/ui/utils/" 2>/dev/null || true

# Count files
V2_COUNT=$(find "$V2/src" -type f \( -name "*.ts" -o -name "*.tsx" \) | wc -l)
echo ""
echo "=== Phase 1 Complete ==="
echo "Files in v2/src: $V2_COUNT"

echo ""
echo "=== Phase 2: Generate Daemon Queue ==="

# Create queue file with daemon commands for React->Preact components
cat > "$QUEUE_FILE" << 'QUEUE'
# React->Preact Component Conversion Queue
# Format: one daemon objective per line
# Run with: while read -r obj; do ./run-daemon.sh "$obj"; done < port-queue.txt

# Priority: Core UI
Port TitleScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/TitleScreen.tsx, convert React imports to Preact (from 'react' -> from 'preact/hooks', className -> class), write to /home/geni/Documents/vale-village-v2/src/ui/components/TitleScreen.tsx using edit_file.
Port MainMenu.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/MainMenu.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/MainMenu.tsx using edit_file.
Port OverworldMap.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/OverworldMap.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/OverworldMap.tsx using edit_file.
Port DialogueBoxV2.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/DialogueBoxV2.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/DialogueBoxV2.tsx using edit_file.
Port PauseMenu.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/PauseMenu.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/PauseMenu.tsx using edit_file.
Port SaveMenu.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/SaveMenu.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/SaveMenu.tsx using edit_file.

# Battle System
Port BattleView.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/BattleView.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/BattleView.tsx using edit_file.
Port QueueBattleView.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/QueueBattleView.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/QueueBattleView.tsx using edit_file.
Port battle/Battlefield.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/Battlefield.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/Battlefield.tsx using edit_file.
Port battle/CommandPanel.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/CommandPanel.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/CommandPanel.tsx using edit_file.
Port battle/AbilityPanel.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/AbilityPanel.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/AbilityPanel.tsx using edit_file.
Port battle/DjinnPanel.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/DjinnPanel.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/DjinnPanel.tsx using edit_file.
Port battle/QueuePanel.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/QueuePanel.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/QueuePanel.tsx using edit_file.
Port battle/TurnOrderStrip.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/TurnOrderStrip.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/TurnOrderStrip.tsx using edit_file.
Port battle/UnitCard.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/UnitCard.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/UnitCard.tsx using edit_file.
Port battle/SidePanelPlayer.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/SidePanelPlayer.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/SidePanelPlayer.tsx using edit_file.
Port battle/SidePanelEnemy.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/SidePanelEnemy.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/SidePanelEnemy.tsx using edit_file.
Port battle/BattleLog.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/BattleLog.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/BattleLog.tsx using edit_file.
Port battle/BattleOverlay.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/BattleOverlay.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/BattleOverlay.tsx using edit_file.
Port battle/StatusIcon.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/StatusIcon.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/StatusIcon.tsx using edit_file.
Port battle/LayoutBattle.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/LayoutBattle.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/LayoutBattle.tsx using edit_file.
Port battle/types.ts from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/battle/types.ts, write to /home/geni/Documents/vale-village-v2/src/ui/components/battle/types.ts using edit_file.

# Screens
Port RewardsScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/RewardsScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/RewardsScreen.tsx using edit_file.
Port PartyManagementScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/PartyManagementScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/PartyManagementScreen.tsx using edit_file.
Port ShopScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/ShopScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/ShopScreen.tsx using edit_file.
Port ShopEquipScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/ShopEquipScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/ShopEquipScreen.tsx using edit_file.
Port DjinnCollectionScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/DjinnCollectionScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/DjinnCollectionScreen.tsx using edit_file.
Port CompendiumScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/CompendiumScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/CompendiumScreen.tsx using edit_file.
Port TowerHubScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/TowerHubScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/TowerHubScreen.tsx using edit_file.
Port CreditsScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/CreditsScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/CreditsScreen.tsx using edit_file.
Port IntroScreen.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/IntroScreen.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/IntroScreen.tsx using edit_file.
Port PreBattleTeamSelectScreenV2.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/PreBattleTeamSelectScreenV2.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/PreBattleTeamSelectScreenV2.tsx using edit_file.

# UI Components
Port BattleUnitSprite.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/BattleUnitSprite.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/BattleUnitSprite.tsx using edit_file.
Port CritMeter.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/CritMeter.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/CritMeter.tsx using edit_file.
Port DjinnBar.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/DjinnBar.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/DjinnBar.tsx using edit_file.
Port ManaCirclesBar.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/ManaCirclesBar.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/ManaCirclesBar.tsx using edit_file.
Port EquipmentIcon.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/EquipmentIcon.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/EquipmentIcon.tsx using edit_file.
Port ChapterIndicator.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/ChapterIndicator.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/ChapterIndicator.tsx using edit_file.
Port VictoryOverlay.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/VictoryOverlay.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/VictoryOverlay.tsx using edit_file.
Port DevModeOverlay.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/DevModeOverlay.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/DevModeOverlay.tsx using edit_file.
Port GameErrorBoundary.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/GameErrorBoundary.tsx, convert to Preact error boundary pattern, write to /home/geni/Documents/vale-village-v2/src/ui/components/GameErrorBoundary.tsx using edit_file.
Port PostBattleCutscene.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/components/PostBattleCutscene.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/components/PostBattleCutscene.tsx using edit_file.

# Sprites (React components)
Port Sprite.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/sprites/Sprite.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/sprites/Sprite.tsx using edit_file.
Port SimpleSprite.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/sprites/SimpleSprite.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/sprites/SimpleSprite.tsx using edit_file.
Port BackgroundSprite.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/sprites/BackgroundSprite.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/sprites/BackgroundSprite.tsx using edit_file.
Port ButtonIcon.tsx from v1 to v2. Read /home/geni/Documents/vale-village/src/ui/sprites/ButtonIcon.tsx, convert React imports to Preact, write to /home/geni/Documents/vale-village-v2/src/ui/sprites/ButtonIcon.tsx using edit_file.
QUEUE

echo "Queue file created: $QUEUE_FILE"
QUEUE_COUNT=$(grep -c "^Port " "$QUEUE_FILE" || true)
echo "Daemon tasks queued: $QUEUE_COUNT"

echo ""
echo "=== Summary ==="
echo "Phase 1 (direct copy): $V2_COUNT files"
echo "Phase 2 (daemon queue): $QUEUE_COUNT tasks"
echo ""
echo "To run daemon queue:"
echo "  cd /home/geni/swarm/memory"
echo "  ./run-daemon-queue.sh"
