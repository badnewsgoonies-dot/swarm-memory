using Godot;
using System;
using ClockworkSpire.Systems;

namespace ClockworkSpire.UI;

/// <summary>
/// UI controller for the upgrade selection screen between floors.
/// </summary>
public partial class UpgradeStationUI : CanvasLayer
{
    private Button[]? _upgradeButtons;
    private Label[]? _nameLabels;
    private Label[]? _descLabels;
    private Button? _rerollButton;
    private Label? _cogsLabel;

    private UpgradeManager? _upgradeManager;

    public override void _Ready()
    {
        // Get node references
        var container = GetNodeOrNull<HBoxContainer>("UpgradeContainer");
        if (container != null)
        {
            _upgradeButtons = new Button[3];
            _nameLabels = new Label[3];
            _descLabels = new Label[3];

            for (int i = 0; i < 3; i++)
            {
                var btn = container.GetNodeOrNull<Button>($"Upgrade{i + 1}");
                if (btn != null)
                {
                    _upgradeButtons[i] = btn;
                    _nameLabels[i] = btn.GetNodeOrNull<Label>("VBox/Name");
                    _descLabels[i] = btn.GetNodeOrNull<Label>("VBox/Desc");

                    int index = i;  // Capture for closure
                    btn.Pressed += () => OnUpgradeSelected(index);
                }
            }
        }

        _rerollButton = GetNodeOrNull<Button>("RerollButton");
        _cogsLabel = GetNodeOrNull<Label>("CogsLabel");

        if (_rerollButton != null)
        {
            _rerollButton.Pressed += OnRerollPressed;
        }

        // Get upgrade manager
        _upgradeManager = GetTree().CurrentScene.GetNodeOrNull<UpgradeManager>("UpgradeManager");
        if (_upgradeManager != null)
        {
            _upgradeManager.UpgradesOffered += RefreshDisplay;
            _upgradeManager.UpgradesRerolled += RefreshDisplay;
        }

        // Initially hidden
        Visible = false;
    }

    public void Show()
    {
        Visible = true;
        _upgradeManager?.GenerateOffers();
        RefreshDisplay();

        // Focus first upgrade button
        _upgradeButtons?[0]?.GrabFocus();

        GameManager.Instance.EnterUpgradeStation();
    }

    public void Hide()
    {
        Visible = false;
        GameManager.Instance.ExitUpgradeStation();
    }

    private void RefreshDisplay()
    {
        if (_upgradeManager == null) return;

        var offers = _upgradeManager.CurrentOffers;

        for (int i = 0; i < 3; i++)
        {
            if (i < offers.Count)
            {
                var upgrade = offers[i];

                if (_nameLabels?[i] != null)
                    _nameLabels[i].Text = upgrade.Name;

                if (_descLabels?[i] != null)
                    _descLabels[i].Text = upgrade.Description;

                if (_upgradeButtons?[i] != null)
                    _upgradeButtons[i].Disabled = false;
            }
            else
            {
                // Hide unused slots
                if (_upgradeButtons?[i] != null)
                    _upgradeButtons[i].Disabled = true;
            }
        }

        // Update cogs display
        if (_cogsLabel != null)
        {
            _cogsLabel.Text = $"Cogs: {_upgradeManager.GetCurrentCogs()}";
        }

        // Update reroll button
        if (_rerollButton != null)
        {
            var cost = _upgradeManager.GetRerollCost();
            var cogs = _upgradeManager.GetCurrentCogs();
            _rerollButton.Text = $"Reroll ({cost})";
            _rerollButton.Disabled = cogs < cost;
        }
    }

    private void OnUpgradeSelected(int index)
    {
        GD.Print($"[UpgradeStationUI] Selected upgrade {index}");

        if (_upgradeManager?.SelectUpgrade(index) == true)
        {
            // Close and continue
            Hide();

            // Advance to next floor
            var roomManager = GetTree().CurrentScene.GetNodeOrNull<RoomManager>("RoomManager");
            if (roomManager != null)
            {
                roomManager.StartFloor(roomManager.CurrentFloor + 1);
            }
        }
    }

    private void OnRerollPressed()
    {
        _upgradeManager?.TryReroll();
    }
}
