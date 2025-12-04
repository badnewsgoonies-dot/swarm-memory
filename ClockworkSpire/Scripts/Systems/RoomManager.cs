using Godot;
using System;
using System.Collections.Generic;

namespace ClockworkSpire.Systems;

/// <summary>
/// Manages room loading, enemy spawning, and door states within a floor.
/// </summary>
public partial class RoomManager : Node
{
    [Export] public PackedScene? CombatRoomScene { get; set; }
    [Export] public PackedScene? BossRoomScene { get; set; }

    // Floor configuration
    public int CurrentFloor { get; private set; } = 1;
    public int CurrentRoomIndex { get; private set; } = 0;
    public int TotalRoomsInFloor { get; private set; } = 4;

    // Room state
    private Node2D? _currentRoom;
    private List<Node2D> _activeEnemies = new();
    private bool _roomCleared = false;

    // Events
    [Signal] public delegate void RoomClearedEventHandler();
    [Signal] public delegate void RoomEnteredEventHandler(int roomIndex);
    [Signal] public delegate void AllEnemiesSpawnedEventHandler();
    [Signal] public delegate void FloorClearedEventHandler(int floor);

    // Enemy spawn definitions per floor/room
    private static readonly Dictionary<int, List<RoomSpawnData>> FloorSpawns = new()
    {
        // Floor 1: 3 combat rooms + implied treasure
        { 1, new List<RoomSpawnData>
            {
                new(2, 0),  // Room 1: 2 Tickers
                new(2, 1),  // Room 2: 2 Tickers, 1 Turret
                new(2, 1),  // Room 3: 2 Tickers, 1 Turret
            }
        },
        // Floor 2: 3 combat rooms, harder
        { 2, new List<RoomSpawnData>
            {
                new(3, 1),  // Room 1: 3 Tickers, 1 Turret
                new(2, 2),  // Room 2: 2 Tickers, 2 Turrets
                new(3, 2),  // Room 3: 3 Tickers, 2 Turrets
            }
        },
    };

    public void StartFloor(int floorNumber)
    {
        CurrentFloor = floorNumber;
        CurrentRoomIndex = 0;
        TotalRoomsInFloor = FloorSpawns.ContainsKey(floorNumber) ? FloorSpawns[floorNumber].Count : 3;

        GD.Print($"[RoomManager] Starting Floor {floorNumber} with {TotalRoomsInFloor} rooms");
        LoadRoom(0);
    }

    public void LoadRoom(int roomIndex)
    {
        CurrentRoomIndex = roomIndex;
        _roomCleared = false;
        _activeEnemies.Clear();

        EmitSignal(SignalName.RoomEntered, roomIndex);
        GD.Print($"[RoomManager] Loaded room {roomIndex + 1}/{TotalRoomsInFloor} on floor {CurrentFloor}");

        // Spawn enemies after delay
        GetTree().CreateTimer(1.0).Timeout += OnSpawnDelay;
    }

    private void OnSpawnDelay()
    {
        SpawnEnemiesForCurrentRoom();
    }

    public void SpawnEnemiesForCurrentRoom()
    {
        if (!FloorSpawns.ContainsKey(CurrentFloor)) return;

        var floorData = FloorSpawns[CurrentFloor];
        if (CurrentRoomIndex >= floorData.Count) return;

        var spawnData = floorData[CurrentRoomIndex];
        GD.Print($"[RoomManager] Spawning {spawnData.TickerCount} Tickers, {spawnData.TurretCount} Turrets");

        // Emit signal for actual spawning (handled by the room scene)
        EmitSignal(SignalName.AllEnemiesSpawned);
    }

    public void RegisterEnemy(Node2D enemy)
    {
        _activeEnemies.Add(enemy);
    }

    public void OnEnemyDied(Node2D enemy)
    {
        _activeEnemies.Remove(enemy);
        GameManager.Instance.Stats.EnemiesKilled++;

        GD.Print($"[RoomManager] Enemy died. Remaining: {_activeEnemies.Count}");

        if (_activeEnemies.Count == 0 && !_roomCleared)
        {
            _roomCleared = true;
            OnRoomCleared();
        }
    }

    private void OnRoomCleared()
    {
        EmitSignal(SignalName.RoomCleared);
        GD.Print("[RoomManager] Room cleared!");

        // Check if floor is complete
        if (CurrentRoomIndex >= TotalRoomsInFloor - 1)
        {
            EmitSignal(SignalName.FloorCleared, CurrentFloor);
            GD.Print($"[RoomManager] Floor {CurrentFloor} cleared!");
        }
    }

    public void AdvanceToNextRoom()
    {
        if (CurrentRoomIndex < TotalRoomsInFloor - 1)
        {
            LoadRoom(CurrentRoomIndex + 1);
        }
    }

    public bool IsRoomCleared() => _roomCleared;
    public int GetActiveEnemyCount() => _activeEnemies.Count;

    public RoomSpawnData GetCurrentRoomSpawns()
    {
        if (!FloorSpawns.ContainsKey(CurrentFloor)) return new RoomSpawnData(0, 0);
        var floorData = FloorSpawns[CurrentFloor];
        if (CurrentRoomIndex >= floorData.Count) return new RoomSpawnData(0, 0);
        return floorData[CurrentRoomIndex];
    }
}

/// <summary>
/// Data for enemy spawns in a room.
/// </summary>
public record RoomSpawnData(int TickerCount, int TurretCount);
