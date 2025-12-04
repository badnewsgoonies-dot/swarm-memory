using Godot;
using System;

namespace ClockworkSpire.Systems;

/// <summary>
/// Global game manager singleton. Handles game state, scene transitions, and run lifecycle.
/// Autoloaded via project.godot.
/// </summary>
public partial class GameManager : Node
{
    public static GameManager Instance { get; private set; } = null!;

    // Game states
    public enum GameState
    {
        MainMenu,
        InRun,
        Paused,
        UpgradeStation,
        GameOver,
        Victory
    }

    public GameState CurrentState { get; private set; } = GameState.MainMenu;

    // Run statistics (reset each run)
    public RunStats Stats { get; private set; } = new();

    // Current player reference
    public Node2D? CurrentPlayer { get; set; }

    // Events
    [Signal] public delegate void StateChangedEventHandler(GameState newState);
    [Signal] public delegate void RunStartedEventHandler();
    [Signal] public delegate void RunEndedEventHandler(bool victory);
    [Signal] public delegate void FloorCompletedEventHandler(int floor);

    public override void _Ready()
    {
        Instance = this;
        ProcessMode = ProcessModeEnum.Always; // Keep running when paused
        GD.Print("[GameManager] Initialized");
    }

    public void StartNewRun()
    {
        Stats = new RunStats();
        Stats.StartTime = Time.GetUnixTimeFromSystem();
        ChangeState(GameState.InRun);
        EmitSignal(SignalName.RunStarted);

        // Load the game scene
        GetTree().ChangeSceneToFile("res://Scenes/Game.tscn");
        GD.Print("[GameManager] New run started");
    }

    public void EndRun(bool victory)
    {
        Stats.EndTime = Time.GetUnixTimeFromSystem();
        Stats.Victory = victory;
        ChangeState(victory ? GameState.Victory : GameState.GameOver);
        EmitSignal(SignalName.RunEnded, victory);
        GD.Print($"[GameManager] Run ended - Victory: {victory}");
    }

    public void ReturnToMainMenu()
    {
        ChangeState(GameState.MainMenu);
        GetTree().ChangeSceneToFile("res://Scenes/Main.tscn");
    }

    public void PauseGame()
    {
        if (CurrentState != GameState.InRun) return;

        GetTree().Paused = true;
        ChangeState(GameState.Paused);
    }

    public void ResumeGame()
    {
        if (CurrentState != GameState.Paused) return;

        GetTree().Paused = false;
        ChangeState(GameState.InRun);
    }

    public void EnterUpgradeStation()
    {
        ChangeState(GameState.UpgradeStation);
    }

    public void ExitUpgradeStation()
    {
        ChangeState(GameState.InRun);
    }

    public void CompleteFloor(int floor)
    {
        Stats.FloorsCleared = floor;
        EmitSignal(SignalName.FloorCompleted, floor);
        GD.Print($"[GameManager] Floor {floor} completed");
    }

    private void ChangeState(GameState newState)
    {
        var oldState = CurrentState;
        CurrentState = newState;
        EmitSignal(SignalName.StateChanged, (int)newState);
        GD.Print($"[GameManager] State: {oldState} -> {newState}");
    }

    public override void _Input(InputEvent @event)
    {
        if (@event.IsActionPressed("pause"))
        {
            if (CurrentState == GameState.InRun)
                PauseGame();
            else if (CurrentState == GameState.Paused)
                ResumeGame();
        }
    }
}

/// <summary>
/// Statistics tracked during a single run.
/// </summary>
public class RunStats
{
    public double StartTime { get; set; }
    public double EndTime { get; set; }
    public int EnemiesKilled { get; set; }
    public int CogsCollected { get; set; }
    public int DamageTaken { get; set; }
    public int FloorsCleared { get; set; }
    public int UpgradesCollected { get; set; }
    public bool Victory { get; set; }

    public double ElapsedSeconds => EndTime > 0 ? EndTime - StartTime : Time.GetUnixTimeFromSystem() - StartTime;

    public string FormattedTime
    {
        get
        {
            var total = (int)ElapsedSeconds;
            var minutes = total / 60;
            var seconds = total % 60;
            return $"{minutes}:{seconds:D2}";
        }
    }
}
