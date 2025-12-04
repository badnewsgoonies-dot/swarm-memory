# Clockwork Crypts: Demo Run

> Game Design Manual - Demo Scope Only
> Version 1.0

---

## 1. Overview

**Clockwork Crypts** is a top-down roguelite shooter set in a steampunk-infested dungeon. The player explores procedurally-arranged rooms, fights clockwork enemies, collects powerups, and defeats a boss to complete a run.

### Demo Scope

This document defines the **minimum viable demo**. Everything here is REQUIRED for the demo to be considered complete. Features marked `[NICE TO HAVE]` are optional stretch goals.

---

## 2. Core Game Loop

```
Title Screen
    |
    v
Start Run -> Room 1 -> Room 2 -> ... -> Boss Room -> Run Summary
                |
                v
           [Player Dies -> Run Summary]
```

### Flow Details

1. **Title Screen**: Shows game title, "Press any key to start"
2. **Start Run**: Player spawns in Room 1 with default stats
3. **Room Progression**: Clear enemies in room -> door opens -> move to next room
4. **Boss Room**: Final room (Room 5) contains the boss
5. **Run Summary**: Shows stats (enemies killed, time, win/lose)

---

## 3. Player

### 3.1 Stats (Demo Values)

| Stat | Default | Description |
|------|---------|-------------|
| HP | 5 | Hearts; die at 0 |
| Speed | 200 | Pixels/second movement |
| Fire Rate | 3/sec | Bullets per second |
| Damage | 1 | Per bullet |

### 3.2 Controls

| Input | Action |
|-------|--------|
| WASD / Arrow Keys | Move |
| Mouse aim | Aim direction |
| Left Click / Space | Shoot |

### 3.3 Visual

- 32x32 sprite
- Simple animation: idle, walk (2 frames), hurt flash
- Health bar above player

---

## 4. Enemies

### 4.1 Basic Clockwork (Required)

- **HP**: 2
- **Behavior**: Move toward player, stop at melee range, attack
- **Damage**: 1 (on contact)
- **Speed**: 80 px/s
- **Visual**: 32x32, gear-themed sprite

### 4.2 Turret (Required)

- **HP**: 3
- **Behavior**: Stationary, shoots at player every 2 seconds
- **Damage**: 1 (projectile)
- **Visual**: 32x32, mounted cannon

### 4.3 Boss: Gear Golem (Required)

- **HP**: 15
- **Phases**:
  - Phase 1 (HP > 8): Slow movement, punches at melee range
  - Phase 2 (HP <= 8): Spawns 2 Basic Clockworks, faster movement
- **Damage**: 2 (melee punch)
- **Visual**: 64x64, large animated sprite

---

## 5. Rooms & Layout

### 5.1 Room Structure

- Each room is 640x480 pixels
- Walls on all edges with one door per exit
- Doors locked until all enemies defeated

### 5.2 Demo Layout (Linear)

```
[Room 1] -> [Room 2] -> [Room 3] -> [Room 4] -> [Boss Room]
  2 basic    3 basic     2 turret    3 basic     Gear Golem
                         1 basic     2 turret
```

Total enemies before boss: 13

### 5.3 Spawning

- Enemies spawn when player enters room
- 1-second delay before enemies activate

---

## 6. Powerups

### 6.1 Health Pickup (Required)

- Restores 1 HP
- Drops from enemies (20% chance)
- Visual: Small red heart

### 6.2 [NICE TO HAVE] Damage Boost

- +1 damage for current room
- Visual: Small orange gear

---

## 7. UI

### 7.1 HUD (Required)

- Player HP (hearts) in top-left
- Current room number in top-right
- Boss HP bar (when in boss room)

### 7.2 Title Screen

- Game title centered
- "Press any key to start" blinking text
- Version number in bottom corner

### 7.3 Run Summary

- "Victory!" or "Defeat" title
- Stats table:
  - Enemies killed
  - Time elapsed
  - Rooms cleared
- "Press any key to return to title"

---

## 8. Audio [NICE TO HAVE]

These are optional for the demo:
- Background music (one loop)
- Shooting SFX
- Enemy hit/death SFX
- Player damage SFX
- Boss phase change SFX

---

## 9. Technical Requirements

### 9.1 Engine/Framework

- Godot 4.x with C# (preferred)
- Alternative: Godot GDScript, Unity, or similar

### 9.2 Target

- Desktop (Windows/Linux)
- 1280x720 window, 2x pixel scaling from 640x360

### 9.3 Build

- Must compile and run without errors
- Single executable or Godot project that can be run from editor

---

## 10. Definition of Demo Complete

The demo is **COMPLETE** when ALL of these criteria are met:

### Core Flow
- [ ] Title screen displays and waits for input
- [ ] Pressing a key starts a new run
- [ ] Player spawns in Room 1 with 5 HP
- [ ] Clearing a room opens the door to the next room
- [ ] Reaching Room 5 spawns the Gear Golem boss
- [ ] Defeating boss shows Run Summary with "Victory!"
- [ ] Dying shows Run Summary with "Defeat"
- [ ] Run Summary allows return to title

### Player
- [ ] Player moves with WASD
- [ ] Player aims with mouse
- [ ] Player shoots bullets
- [ ] Player takes damage from enemies
- [ ] Player HP displays in HUD
- [ ] Player dies when HP reaches 0

### Enemies
- [ ] Basic Clockwork spawns, moves toward player, deals damage
- [ ] Turret spawns, shoots projectiles at player
- [ ] Gear Golem has 2 phases with different behaviors
- [ ] All enemies can be killed with enough damage

### Rooms
- [ ] 5 rooms total (4 regular + 1 boss)
- [ ] Doors are locked until room is cleared
- [ ] Each room spawns correct enemy composition

### Polish
- [ ] Health pickup drops from enemies
- [ ] All sprites visible (placeholders OK if distinct)
- [ ] No crashes during normal gameplay

### Build
- [ ] Project compiles without errors
- [ ] Game runs from editor or built executable
- [ ] Full run is playable from title to summary

---

## 11. Testing Checklist

Use this checklist to verify the demo:

1. Launch game -> See title screen
2. Press key -> Start in Room 1
3. Move WASD -> Player moves
4. Aim mouse -> Player faces cursor
5. Click/Space -> Player shoots
6. Kill 2 basic enemies -> Door opens
7. Enter Room 2 -> New enemies spawn
8. Take damage -> HP decreases, flash effect
9. Die -> See "Defeat" summary
10. Restart -> See title, start new run
11. Clear all 5 rooms -> Beat boss -> See "Victory" summary
12. Time: Full run should take 3-5 minutes

---

*This document is the single source of truth for the Clockwork Crypts demo.*
