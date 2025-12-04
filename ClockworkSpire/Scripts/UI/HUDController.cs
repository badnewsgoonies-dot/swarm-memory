using Godot;
using System;
using ClockworkSpire.Systems;
using ClockworkSpire.Player;
using ClockworkSpire.Enemies;

namespace ClockworkSpire.UI;

/// <summary>
/// HUD controller for in-game UI elements.
/// </summary>
public partial class HUDController : CanvasLayer
{
    // Node references (assigned in scene)
    private HBoxContainer? _healthContainer;
    private Label? _floorLabel;
    private Label? _roomLabel;
    private Label? _cogsLabel;
    private Control? _bossHealthBar;
    private ProgressBar? _bossProgress;
    private Label? _bossNameLabel;

    // Health icon scene
    [Export] public PackedScene? HealthIconScene { get; set; }

    // State
    private int _displayedHealth = 0;
    private int _displayedMaxHealth = 0;

    public override void _Ready()
    {
        // Get node references
        _healthContainer = GetNodeOrNull<HBoxContainer>("TopBar/HealthContainer");
        _floorLabel = GetNodeOrNull<Label>("TopBar/FloorLabel");
        _roomLabel = GetNodeOrNull<Label>("TopBar/RoomLabel");
        _cogsLabel = GetNodeOrNull<Label>("TopBar/CogsLabel");
        _bossHealthBar = GetNodeOrNull<Control>("BossHealthBar");
        _bossProgress = _bossHealthBar?.GetNodeOrNull<ProgressBar>("ProgressBar");
        _bossNameLabel = _bossHealthBar?.GetNodeOrNull<Label>("BossName");

        // Hide boss bar initially
        if (_bossHealthBar != null)
            _bossHealthBar.Visible = false;

        // Connect to game events
        var gameManager = GameManager.Instance;
        if (gameManager != null)
        {
            gameManager.StateChanged += OnGameStateChanged;
        }

        GD.Print("[HUD] Initialized");
    }

    public override void _Process(double delta)
    {
        // Update cogs display
        UpdateCogsDisplay();
    }

    public void ConnectToPlayer(PlayerController player)
    {
        player.HealthChanged += OnPlayerHealthChanged;
        OnPlayerHealthChanged(player.Stats.CurrentHP, player.Stats.MaxHP);
    }

    public void ConnectToRoomManager(RoomManager roomManager)
    {
        roomManager.RoomEntered += OnRoomEntered;
        roomManager.FloorCleared += OnFloorCleared;
    }

    public void ConnectToBoss(BrassGuardian boss)
    {
        ShowBossHealthBar(boss);
        boss.DamageTaken += (remaining) => UpdateBossHealth(boss);
        boss.PhaseChanged += OnBossPhaseChanged;
    }

    private void OnPlayerHealthChanged(int current, int max)
    {
        if (_healthContainer == null) return;

        // Only rebuild if max changed
        if (max != _displayedMaxHealth)
        {
            RebuildHealthIcons(max);
            _displayedMaxHealth = max;
        }

        // Update filled state
        UpdateHealthIcons(current);
        _displayedHealth = current;
    }

    private void RebuildHealthIcons(int maxHealth)
    {
        if (_healthContainer == null) return;

        // Clear existing icons
        foreach (var child in _healthContainer.GetChildren())
        {
            child.QueueFree();
        }

        // Create new icons
        for (int i = 0; i < maxHealth; i++)
        {
            if (HealthIconScene != null)
            {
                var icon = HealthIconScene.Instantiate<Control>();
                _healthContainer.AddChild(icon);
            }
            else
            {
                // Fallback: create simple label
                var label = new Label();
                label.Text = "[*]";
                label.Name = $"Health{i}";
                _healthContainer.AddChild(label);
            }
        }
    }

    private void UpdateHealthIcons(int current)
    {
        if (_healthContainer == null) return;

        var children = _healthContainer.GetChildren();
        for (int i = 0; i < children.Count; i++)
        {
            var child = children[i];
            bool isFilled = i < current;

            // Update visual based on filled state
            if (child is TextureRect textureRect)
            {
                textureRect.Modulate = isFilled ? Colors.White : new Color(0.3f, 0.3f, 0.3f);
            }
            else if (child is Label label)
            {
                label.Text = isFilled ? "[*]" : "[ ]";
                label.Modulate = isFilled ? Colors.White : new Color(0.5f, 0.5f, 0.5f);
            }
        }
    }

    public void UpdateFloorDisplay(int floor)
    {
        if (_floorLabel != null)
            _floorLabel.Text = $"Floor {floor}";
    }

    private void OnRoomEntered(int roomIndex)
    {
        if (_roomLabel != null)
        {
            var roomManager = GetTree().CurrentScene.GetNodeOrNull<RoomManager>("RoomManager");
            var total = roomManager?.TotalRoomsInFloor ?? 4;
            _roomLabel.Text = $"Room {roomIndex + 1}/{total}";
        }
    }

    private void OnFloorCleared(int floor)
    {
        // Flash or highlight floor complete
        if (_floorLabel != null)
        {
            var tween = CreateTween();
            tween.TweenProperty(_floorLabel, "modulate", new Color(1.5f, 1.5f, 0.5f), 0.2f);
            tween.TweenProperty(_floorLabel, "modulate", Colors.White, 0.2f);
        }
    }

    private void UpdateCogsDisplay()
    {
        if (_cogsLabel != null)
        {
            var cogs = GameManager.Instance?.Stats.CogsCollected ?? 0;
            _cogsLabel.Text = $"Cogs: {cogs}";
        }
    }

    public void ShowBossHealthBar(BrassGuardian boss)
    {
        if (_bossHealthBar == null) return;

        _bossHealthBar.Visible = true;

        if (_bossNameLabel != null)
            _bossNameLabel.Text = "BRASS GUARDIAN";

        if (_bossProgress != null)
        {
            _bossProgress.MaxValue = boss.MaxHP;
            _bossProgress.Value = boss.CurrentHP;
        }
    }

    private void UpdateBossHealth(BrassGuardian boss)
    {
        if (_bossProgress != null)
        {
            _bossProgress.Value = boss.CurrentHP;
        }
    }

    private void OnBossPhaseChanged(int phase)
    {
        if (_bossProgress == null) return;

        // Change color for phase 2
        if (phase == 2)
        {
            // Tween to red
            var tween = CreateTween();
            var styleBox = _bossProgress.GetThemeStylebox("fill") as StyleBoxFlat;
            if (styleBox != null)
            {
                // Note: In real implementation, modify the progress bar's fill color
            }

            // Flash effect
            tween.TweenProperty(_bossHealthBar, "modulate", new Color(1.5f, 0.5f, 0.5f), 0.2f);
            tween.TweenProperty(_bossHealthBar, "modulate", Colors.White, 0.2f);
        }
    }

    public void HideBossHealthBar()
    {
        if (_bossHealthBar != null)
            _bossHealthBar.Visible = false;
    }

    private void OnGameStateChanged(GameManager.GameState newState)
    {
        // Hide HUD when not in run
        Visible = newState == GameManager.GameState.InRun ||
                  newState == GameManager.GameState.Paused;
    }
}
