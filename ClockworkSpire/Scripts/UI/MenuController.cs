using Godot;
using System;
using ClockworkSpire.Systems;

namespace ClockworkSpire.UI;

/// <summary>
/// Controller for title screen, pause menu, and run summary screens.
/// </summary>
public partial class MenuController : CanvasLayer
{
    // Title Screen nodes
    private Control? _titleScreen;
    private Label? _pressStartLabel;

    // Pause Menu nodes
    private Control? _pauseMenu;

    // Run Summary nodes
    private Control? _runSummary;
    private Label? _outcomeLabel;
    private Label? _enemiesKilledLabel;
    private Label? _cogsCollectedLabel;
    private Label? _damageTakenLabel;
    private Label? _timeLabel;
    private Label? _upgradesLabel;

    // Animation state
    private float _blinkTimer = 0f;
    private bool _canAcceptInput = true;

    public override void _Ready()
    {
        // Get screen references
        _titleScreen = GetNodeOrNull<Control>("TitleScreen");
        _pressStartLabel = _titleScreen?.GetNodeOrNull<Label>("PressStart");

        _pauseMenu = GetNodeOrNull<Control>("PauseMenu");

        _runSummary = GetNodeOrNull<Control>("RunSummary");
        _outcomeLabel = _runSummary?.GetNodeOrNull<Label>("Outcome");
        _enemiesKilledLabel = _runSummary?.GetNodeOrNull<Label>("Stats/EnemiesKilled");
        _cogsCollectedLabel = _runSummary?.GetNodeOrNull<Label>("Stats/CogsCollected");
        _damageTakenLabel = _runSummary?.GetNodeOrNull<Label>("Stats/DamageTaken");
        _timeLabel = _runSummary?.GetNodeOrNull<Label>("Stats/Time");
        _upgradesLabel = _runSummary?.GetNodeOrNull<Label>("Stats/Upgrades");

        // Connect to button signals if present
        ConnectButtons();

        // Connect to game manager
        var gameManager = GameManager.Instance;
        if (gameManager != null)
        {
            gameManager.StateChanged += OnGameStateChanged;
            gameManager.RunEnded += OnRunEnded;
        }

        // Initial state
        ShowTitleScreen();
    }

    private void ConnectButtons()
    {
        // Pause menu buttons
        var resumeBtn = _pauseMenu?.GetNodeOrNull<Button>("ResumeButton");
        resumeBtn?.Pressed += OnResumePressed;

        var quitBtn = _pauseMenu?.GetNodeOrNull<Button>("QuitButton");
        quitBtn?.Pressed += OnQuitPressed;
    }

    public override void _Process(double delta)
    {
        // Blink "Press Start" text
        if (_titleScreen?.Visible == true && _pressStartLabel != null)
        {
            _blinkTimer += (float)delta;
            _pressStartLabel.Visible = ((int)(_blinkTimer * 2) % 2) == 0;
        }
    }

    public override void _Input(InputEvent @event)
    {
        if (!_canAcceptInput) return;

        // Title screen - any key starts game
        if (_titleScreen?.Visible == true)
        {
            if (@event.IsActionPressed("ui_accept") ||
                (@event is InputEventKey keyEvent && keyEvent.Pressed))
            {
                StartGame();
            }
        }

        // Run summary - any key returns to title
        if (_runSummary?.Visible == true)
        {
            if (@event.IsActionPressed("ui_accept") ||
                (@event is InputEventKey keyEvent2 && keyEvent2.Pressed))
            {
                ReturnToTitle();
            }
        }
    }

    private void OnGameStateChanged(GameManager.GameState newState)
    {
        HideAll();

        switch (newState)
        {
            case GameManager.GameState.MainMenu:
                ShowTitleScreen();
                break;
            case GameManager.GameState.Paused:
                ShowPauseMenu();
                break;
            case GameManager.GameState.Victory:
            case GameManager.GameState.GameOver:
                // Summary is shown via OnRunEnded
                break;
        }
    }

    private void OnRunEnded(bool victory)
    {
        ShowRunSummary(victory);
    }

    public void ShowTitleScreen()
    {
        HideAll();
        if (_titleScreen != null)
        {
            _titleScreen.Visible = true;
            _blinkTimer = 0f;
        }
    }

    public void ShowPauseMenu()
    {
        if (_pauseMenu != null)
        {
            _pauseMenu.Visible = true;

            // Focus resume button
            var resumeBtn = _pauseMenu.GetNodeOrNull<Button>("ResumeButton");
            resumeBtn?.GrabFocus();
        }
    }

    public void ShowRunSummary(bool victory)
    {
        HideAll();
        if (_runSummary == null) return;

        _runSummary.Visible = true;

        // Set outcome
        if (_outcomeLabel != null)
        {
            _outcomeLabel.Text = victory ? "VICTORY!" : "DEFEAT";
            _outcomeLabel.Modulate = victory ? new Color(1, 0.8f, 0.2f) : new Color(0.8f, 0.2f, 0.2f);
        }

        // Fill in stats
        var stats = GameManager.Instance?.Stats;
        if (stats != null)
        {
            if (_enemiesKilledLabel != null)
                _enemiesKilledLabel.Text = $"Enemies Defeated: {stats.EnemiesKilled}";

            if (_cogsCollectedLabel != null)
                _cogsCollectedLabel.Text = $"Cogs Collected: {stats.CogsCollected}";

            if (_damageTakenLabel != null)
                _damageTakenLabel.Text = $"Damage Taken: {stats.DamageTaken}";

            if (_timeLabel != null)
                _timeLabel.Text = $"Time: {stats.FormattedTime}";

            if (_upgradesLabel != null)
                _upgradesLabel.Text = $"Upgrades: {stats.UpgradesCollected}";
        }

        // Delay input acceptance briefly
        _canAcceptInput = false;
        GetTree().CreateTimer(1.0).Timeout += () => _canAcceptInput = true;
    }

    private void HideAll()
    {
        if (_titleScreen != null) _titleScreen.Visible = false;
        if (_pauseMenu != null) _pauseMenu.Visible = false;
        if (_runSummary != null) _runSummary.Visible = false;
    }

    private void StartGame()
    {
        GD.Print("[Menu] Starting new game");
        _canAcceptInput = false;

        // Fade out and start
        var tween = CreateTween();
        tween.TweenProperty(_titleScreen, "modulate:a", 0f, 0.3f);
        tween.TweenCallback(Callable.From(() =>
        {
            GameManager.Instance?.StartNewRun();
            _canAcceptInput = true;
        }));
    }

    private void ReturnToTitle()
    {
        GD.Print("[Menu] Returning to title");
        _canAcceptInput = false;

        var tween = CreateTween();
        tween.TweenProperty(_runSummary, "modulate:a", 0f, 0.3f);
        tween.TweenCallback(Callable.From(() =>
        {
            GameManager.Instance?.ReturnToMainMenu();
            _canAcceptInput = true;
        }));
    }

    private void OnResumePressed()
    {
        GameManager.Instance?.ResumeGame();
        _pauseMenu!.Visible = false;
    }

    private void OnQuitPressed()
    {
        GameManager.Instance?.EndRun(victory: false);
    }
}
