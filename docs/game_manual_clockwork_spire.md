# Clockwork Spire: Ascension Protocol

> Game Design Manual - Vertical Slice v1.0
> A Top-Down Roguelite for Godot 4 (C#)

---

## 1. World & Theme

### 1.1 Setting

**The Clockwork Spire** rises from the ruins of the old world—a towering mechanical fortress built by the Artificer Guild before their mysterious disappearance. Legends speak of the **Ascension Engine** at the Spire's peak, a device capable of granting its operator dominion over time itself.

You are a **Salvager**, one of many who venture into the Spire seeking fortune, power, or simply survival. The Spire is alive with **Constructs**—autonomous machines that defend its secrets. Each floor tests your skill and adaptability. Reach the top. Claim the Engine. Ascend.

### 1.2 Visual Style

- **Steampunk-mechanical**: Brass, copper, gears, steam vents, glowing energy cores
- **Color palette**: Deep bronze, teal energy, rust orange accents, dark steel backgrounds
- **Perspective**: Top-down with slight perspective for depth
- **Scale**: 32x32 base tile size, 2x pixel scaling (640x360 → 1280x720)

---

## 2. Core Game Loop

```
┌─────────────┐
│ Title Screen│
└──────┬──────┘
       │ [Start Run]
       v
┌─────────────┐    ┌─────────────┐
│  Hub Room   │───>│   Floor 1   │
│ (Loadout)   │    │ 3-4 Rooms   │
└─────────────┘    └──────┬──────┘
                          │ [Clear Floor]
       ┌──────────────────┴───────────────────┐
       v                                      v
┌─────────────┐                       ┌─────────────┐
│ Upgrade     │                       │   Floor 2   │
│ Station     │──────────────────────>│ 3-4 Rooms   │
└─────────────┘                       └──────┬──────┘
                                             │
                                             v
                                     ┌─────────────┐
                                     │ Boss Floor  │
                                     │ (Guardian)  │
                                     └──────┬──────┘
                                             │
              ┌──────────────────────────────┴───────────┐
              v                                          v
       ┌─────────────┐                           ┌─────────────┐
       │  VICTORY    │                           │   DEFEAT    │
       │  Summary    │                           │  Summary    │
       └─────────────┘                           └─────────────┘
              │                                          │
              └──────────────┬───────────────────────────┘
                             v
                      [Return to Title]
```

### 2.1 Run Structure

1. **Title Screen**: Game logo, "Press ENTER to Begin"
2. **Hub Room**: Player spawns, can view stats, starts run
3. **Floor 1**: 3-4 combat rooms, clear all to advance
4. **Upgrade Station**: Choose 1 of 3 random upgrades
5. **Floor 2**: 3-4 harder rooms with mixed enemy types
6. **Boss Floor**: Single room with the Brass Guardian
7. **Run Summary**: Statistics and outcome

### 2.2 Room Flow

1. Player enters room
2. Doors seal (if enemies present)
3. 1-second warning before enemies activate
4. Combat until all enemies defeated
5. Doors unseal, loot drops
6. Player chooses exit

---

## 3. The Salvager (Player)

### 3.1 Base Statistics

| Stat | Default | Description |
|------|---------|-------------|
| **Max HP** | 6 | Total health (shown as cogs) |
| **Move Speed** | 220 | Units per second |
| **Fire Rate** | 4.0 | Shots per second |
| **Damage** | 1 | Per projectile |
| **Crit Chance** | 5% | Double damage chance |
| **Cog Magnet** | 48px | Pickup collection radius |

### 3.2 Controls

| Input | Action |
|-------|--------|
| **WASD** | Move in 8 directions |
| **Mouse** | Aim direction |
| **Left Click** | Primary fire (hold to auto-fire) |
| **Right Click** | Dash (if unlocked) |
| **E** | Interact / Pick up |
| **ESC** | Pause menu |
| **TAB** | View current upgrades |

### 3.3 Movement

- 8-directional movement with diagonal normalization
- Smooth acceleration/deceleration (0.1s ramp)
- Cannot pass through walls or enemies
- Collision box: 24x24 centered on 32x32 sprite

### 3.4 Combat

**Primary Fire (Salvager's Pistol)**:
- Fires energy bolts toward mouse cursor
- Projectile speed: 600 units/second
- Projectile lifetime: 1.5 seconds
- Piercing: No (stops on first hit)

**Invincibility Frames**:
- 0.5 seconds after taking damage
- Visual: Sprite flashes/blinks

### 3.5 Visuals

- 32x32 animated sprite
- States: Idle, Walk (4 frames), Hurt, Death
- Directional facing (8 directions or 4 with flip)
- Health displayed as cog icons in HUD

---

## 4. Constructs (Enemies)

### 4.1 Ticker (Basic Melee)

> "Small, fast, and numerous. Tickers swarm intruders with reckless abandon."

| Stat | Value |
|------|-------|
| **HP** | 2 |
| **Damage** | 1 (contact) |
| **Speed** | 140 units/s |
| **Size** | 24x24 |
| **Behavior** | Chase player directly |

**Behavior Details**:
- Spawns in groups of 2-4
- Moves directly toward player
- Deals damage on collision
- Brief stun (0.3s) after hitting player
- Death: Small gear explosion

### 4.2 Sprocket Turret (Ranged)

> "Mounted sentries that track movement. Patience or speed—choose wisely."

| Stat | Value |
|------|-------|
| **HP** | 4 |
| **Damage** | 1 (projectile) |
| **Fire Rate** | 0.5/s (every 2s) |
| **Range** | 300 units |
| **Size** | 32x32 |
| **Behavior** | Stationary, aims at player |

**Behavior Details**:
- Does not move
- Rotates to track player
- Fires slow-moving projectile (300 units/s)
- Projectile is visible (can be dodged)
- Warning: Barrel glows 0.5s before firing

### 4.3 Brass Guardian (Boss)

> "The floor's keeper. Ancient. Powerful. Patient."

| Stat | Value |
|------|-------|
| **HP** | 25 |
| **Size** | 64x64 |
| **Contact Damage** | 2 |

**Phase 1: Patrol (HP > 15)**
- Moves slowly toward player (60 units/s)
- Every 3 seconds: Pounds ground, creating shockwave
- Shockwave: Ring that expands outward (must be dodged)
- Shockwave damage: 2

**Phase 2: Rage (HP <= 15)**
- Speed increases to 100 units/s
- Spawns 2 Tickers every 5 seconds
- Shockwave frequency: Every 2 seconds
- Visual: Glowing eyes, steam vents active

**Phase Transition**:
- 1-second stun when entering Phase 2
- Screen shake
- Boss roar (visual effect)

---

## 5. Floors & Rooms

### 5.1 Room Specifications

| Property | Value |
|----------|-------|
| **Size** | 640x480 pixels (20x15 tiles) |
| **Tile Size** | 32x32 |
| **Wall Thickness** | 1 tile (32px) |
| **Door Width** | 2 tiles (64px) |

### 5.2 Room Types

**Combat Room**:
- Contains enemies
- Doors lock until cleared
- May contain destructible obstacles

**Treasure Room** (1 per floor):
- Contains upgrade item
- No enemies
- Marked with special door

**Boss Room**:
- Larger: 800x600 pixels
- Contains Guardian
- No exit until victory

### 5.3 Floor Layout (Demo)

**Floor 1** (3 rooms + treasure):
```
[Start] → [Combat A] → [Combat B] → [Treasure] → [Exit]
              2T           2T+1S         Item
```
- Combat A: 2 Tickers
- Combat B: 2 Tickers + 1 Sprocket Turret
- Total: 4 Tickers, 1 Turret

**Floor 2** (3 rooms):
```
[Entry] → [Combat C] → [Combat D] → [Exit to Boss]
             3T+1S        2T+2S
```
- Combat C: 3 Tickers + 1 Sprocket Turret
- Combat D: 2 Tickers + 2 Sprocket Turrets
- Total: 5 Tickers, 3 Turrets

**Boss Floor**:
```
[Boss Room: Brass Guardian]
```

### 5.4 Spawn Rules

- Enemies spawn at designated points (not too close to player)
- Minimum spawn distance from player: 150 units
- 1-second delay before enemies become active
- Visual: Enemies materialize with gear-spin effect

---

## 6. Upgrades & Items

### 6.1 Upgrade System

After clearing Floor 1, player chooses 1 of 3 random upgrades:

| Upgrade | Effect | Rarity |
|---------|--------|--------|
| **Overclock** | +20% fire rate | Common |
| **Reinforced Frame** | +2 Max HP | Common |
| **Piercing Rounds** | Projectiles pierce 1 enemy | Rare |
| **Quick Gears** | +15% move speed | Common |
| **Scrap Magnet** | +50% pickup radius | Common |
| **Critical Tuning** | +10% crit chance | Rare |

### 6.2 Pickups

**Cog (Currency)**:
- Dropped by enemies (100% on death)
- Value: 1 cog per enemy
- Used at upgrade station (3 cogs = reroll upgrades)

**Repair Kit (Health)**:
- Dropped by enemies (15% chance)
- Restores 1 HP
- Visual: Red cog with + symbol

### 6.3 Upgrade UI

```
┌─────────────────────────────────────────┐
│         UPGRADE STATION                 │
├─────────────────────────────────────────┤
│  [1]           [2]           [3]        │
│ ┌─────┐     ┌─────┐      ┌─────┐       │
│ │     │     │     │      │     │       │
│ │ OVR │     │ REI │      │ PIE │       │
│ │     │     │     │      │     │       │
│ └─────┘     └─────┘      └─────┘       │
│ Overclock  Reinforced   Piercing       │
│ +20% Rate   +2 HP       Pierce 1       │
│                                         │
│      [R] Reroll (3 Cogs)               │
│         Cogs: 5                         │
└─────────────────────────────────────────┘
```

---

## 7. User Interface

### 7.1 HUD Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [⚙][⚙][⚙][⚙][⚙][⚙]                     Floor 1 | Room 2/4 │
│ HP: 6/6                                         Cogs: 12   │
│                                                             │
│                                                             │
│                       (Play Area)                           │
│                                                             │
│                                                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 Boss Health Bar

```
┌─────────────────────────────────────────────────────────────┐
│              BRASS GUARDIAN                                 │
│ [████████████████████░░░░░░░░░░░] 15/25                    │
└─────────────────────────────────────────────────────────────┘
```
- Appears at top-center during boss fight
- Shows name and HP bar
- Changes color in Phase 2 (orange → red)

### 7.3 Title Screen

```
    ╔═══════════════════════════════════════╗
    ║                                       ║
    ║         CLOCKWORK SPIRE               ║
    ║       ASCENSION PROTOCOL              ║
    ║                                       ║
    ║         [Press ENTER]                 ║
    ║                                       ║
    ║                          v0.1 Demo    ║
    ╚═══════════════════════════════════════╝
```

### 7.4 Run Summary

```
    ┌─────────────────────────────────────┐
    │           ★ VICTORY ★               │
    │      The Spire Falls Silent         │
    ├─────────────────────────────────────┤
    │  Enemies Defeated:     23           │
    │  Cogs Collected:       18           │
    │  Damage Taken:         8            │
    │  Time:                 4:32         │
    │  Upgrades:             2            │
    ├─────────────────────────────────────┤
    │       [Press ENTER to Continue]     │
    └─────────────────────────────────────┘
```

### 7.5 Pause Menu

```
    ┌─────────────────────────┐
    │       PAUSED            │
    ├─────────────────────────┤
    │   [Resume]              │
    │   [Upgrades]            │
    │   [Quit Run]            │
    └─────────────────────────┘
```

---

## 8. Technical Specifications

### 8.1 Engine & Language

- **Engine**: Godot 4.x
- **Language**: C# (Mono/.NET)
- **NOT GDScript**: All logic in C#

### 8.2 Project Structure

```
ClockworkSpire/
├── project.godot
├── ClockworkSpire.csproj
├── Scenes/
│   ├── Main.tscn              # Entry point
│   ├── Game.tscn              # Main game scene
│   ├── UI/
│   │   ├── TitleScreen.tscn
│   │   ├── HUD.tscn
│   │   ├── PauseMenu.tscn
│   │   ├── UpgradeStation.tscn
│   │   └── RunSummary.tscn
│   ├── Player/
│   │   └── Player.tscn
│   ├── Enemies/
│   │   ├── Ticker.tscn
│   │   ├── SprocketTurret.tscn
│   │   └── BrassGuardian.tscn
│   ├── Rooms/
│   │   ├── RoomBase.tscn
│   │   ├── CombatRoom.tscn
│   │   └── BossRoom.tscn
│   └── Effects/
│       ├── Projectile.tscn
│       └── Shockwave.tscn
├── Scripts/
│   ├── Player/
│   │   ├── PlayerController.cs
│   │   ├── PlayerStats.cs
│   │   └── PlayerCombat.cs
│   ├── Enemies/
│   │   ├── EnemyBase.cs
│   │   ├── Ticker.cs
│   │   ├── SprocketTurret.cs
│   │   └── BrassGuardian.cs
│   ├── Systems/
│   │   ├── GameManager.cs
│   │   ├── RunManager.cs
│   │   ├── RoomManager.cs
│   │   └── UpgradeManager.cs
│   └── UI/
│       ├── HUDController.cs
│       └── MenuController.cs
├── Resources/
│   ├── Upgrades/
│   │   └── UpgradeData.cs     # Resource definitions
│   └── Enemies/
│       └── EnemyData.cs
└── Assets/
    ├── Sprites/
    ├── Audio/
    └── Fonts/
```

### 8.3 Resolution & Scaling

| Property | Value |
|----------|-------|
| **Base Resolution** | 640x360 |
| **Window Size** | 1280x720 |
| **Pixel Scale** | 2x |
| **Stretch Mode** | canvas_items |
| **Stretch Aspect** | keep |

### 8.4 Input Map

```
// In project.godot [input] section
move_up = Key.W, Key.Up
move_down = Key.S, Key.Down
move_left = Key.A, Key.Left
move_right = Key.D, Key.Right
fire = MouseButton.Left, Key.Space
dash = MouseButton.Right
interact = Key.E
pause = Key.Escape
view_upgrades = Key.Tab
ui_accept = Key.Enter
```

### 8.5 Build Requirements

- `dotnet build` must pass without errors
- `godot4 --headless --check-only` should validate project
- Single scene launch from `Scenes/Main.tscn`

---

## 9. Glossary

| Term | Definition |
|------|------------|
| **Construct** | Any enemy machine in the Spire |
| **Cog** | Currency dropped by enemies |
| **Salvager** | The player character |
| **Floor** | A collection of rooms to clear |
| **Run** | A single playthrough from start to victory/defeat |
| **Upgrade** | Permanent buff for the current run |
| **Guardian** | Boss enemy at the end of each major section |
| **Shockwave** | Expanding ring attack that must be jumped/dashed |
| **Ticker** | Small melee Construct |
| **Sprocket** | Turret-type Construct |
| **Ascension Engine** | The MacGuffin at the top of the Spire |

---

## 10. Definition of Vertical Slice Complete

### Required Criteria

#### Core Flow
- [ ] Title screen displays and accepts input
- [ ] New run starts with player in hub/starting room
- [ ] Floor 1 has 3-4 clearable rooms
- [ ] Upgrade station appears after Floor 1
- [ ] Floor 2 has 3-4 clearable rooms
- [ ] Boss floor spawns Brass Guardian
- [ ] Victory screen on boss defeat
- [ ] Defeat screen on player death
- [ ] Return to title from summary

#### Player
- [ ] 8-directional WASD movement
- [ ] Mouse aiming
- [ ] Auto-fire on left click hold
- [ ] Takes damage with i-frames
- [ ] Dies at 0 HP
- [ ] Collects pickups

#### Enemies
- [ ] Ticker: Spawns, chases, deals contact damage, dies
- [ ] Sprocket Turret: Stationary, aims, shoots, dies
- [ ] Brass Guardian: Two phases, shockwave, spawns adds

#### Upgrades
- [ ] At least 3 different upgrades implemented
- [ ] Upgrade selection UI works
- [ ] Upgrades visibly affect gameplay

#### UI
- [ ] HUD shows HP, floor, room, cogs
- [ ] Boss HP bar during boss fight
- [ ] Pause menu functional
- [ ] Run summary shows stats

#### Technical
- [ ] `dotnet build` succeeds
- [ ] No crashes during normal play
- [ ] Consistent 60 FPS

---

## 11. Testing Checklist

1. **Launch** → Title screen appears
2. **Start** → Press ENTER → Game begins
3. **Move** → WASD moves player in 8 directions
4. **Aim** → Mouse cursor controls aim direction
5. **Shoot** → Left click fires projectiles
6. **Combat** → Kill a Ticker → It drops cog
7. **Damage** → Get hit → HP decreases, flash occurs
8. **Clear Room** → Kill all enemies → Door opens
9. **Progress** → Enter next room → New enemies spawn
10. **Upgrade** → Clear Floor 1 → See upgrade choices
11. **Select** → Click upgrade → Stat changes
12. **Boss** → Reach boss room → Guardian spawns
13. **Phase 2** → Damage boss to 15 HP → Behavior changes
14. **Victory** → Kill boss → Victory screen
15. **Defeat** → Die → Defeat screen
16. **Restart** → From summary → Return to title

---

*This document is the single source of truth for Clockwork Spire vertical slice development.*
*All implementations must match these specifications.*
*Update this document if design changes are made.*
