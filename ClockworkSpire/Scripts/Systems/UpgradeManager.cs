using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using ClockworkSpire.Player;

namespace ClockworkSpire.Systems;

/// <summary>
/// Manages upgrade offerings and selection between floors.
/// </summary>
public partial class UpgradeManager : Node
{
    [Export] public int OfferedUpgradeCount { get; set; } = 3;
    [Export] public int RerollCost { get; set; } = 3;

    // Available upgrade definitions
    private static readonly List<UpgradeDefinition> AllUpgrades = new()
    {
        new UpgradeDefinition("Overclock", "overclock", "+20% Fire Rate", UpgradeRarity.Common),
        new UpgradeDefinition("Reinforced Frame", "reinforced_frame", "+2 Max HP", UpgradeRarity.Common),
        new UpgradeDefinition("Quick Gears", "quick_gears", "+15% Move Speed", UpgradeRarity.Common),
        new UpgradeDefinition("Scrap Magnet", "scrap_magnet", "+50% Pickup Radius", UpgradeRarity.Common),
        new UpgradeDefinition("Piercing Rounds", "piercing_rounds", "Shots Pierce 1 Enemy", UpgradeRarity.Rare),
        new UpgradeDefinition("Critical Tuning", "critical_tuning", "+10% Crit Chance", UpgradeRarity.Rare),
    };

    // Current offered upgrades
    public List<UpgradeDefinition> CurrentOffers { get; private set; } = new();

    // Events
    [Signal] public delegate void UpgradesOfferedEventHandler();
    [Signal] public delegate void UpgradeSelectedEventHandler(string upgradeId);
    [Signal] public delegate void UpgradesRerolledEventHandler();

    public void GenerateOffers()
    {
        CurrentOffers.Clear();

        // Weighted random selection based on rarity
        var pool = new List<UpgradeDefinition>(AllUpgrades);
        var random = new Random();

        for (int i = 0; i < OfferedUpgradeCount && pool.Count > 0; i++)
        {
            // Weight by rarity: Common = 3, Rare = 1
            var weightedPool = new List<UpgradeDefinition>();
            foreach (var upgrade in pool)
            {
                int weight = upgrade.Rarity == UpgradeRarity.Common ? 3 : 1;
                for (int w = 0; w < weight; w++)
                {
                    weightedPool.Add(upgrade);
                }
            }

            var selected = weightedPool[random.Next(weightedPool.Count)];
            CurrentOffers.Add(selected);
            pool.Remove(selected);  // No duplicates
        }

        EmitSignal(SignalName.UpgradesOffered);
        GD.Print($"[UpgradeManager] Offering {CurrentOffers.Count} upgrades");
    }

    public bool SelectUpgrade(int index)
    {
        if (index < 0 || index >= CurrentOffers.Count)
        {
            GD.PrintErr($"[UpgradeManager] Invalid upgrade index: {index}");
            return false;
        }

        var upgrade = CurrentOffers[index];

        // Apply to player
        var player = GameManager.Instance.CurrentPlayer as PlayerController;
        if (player != null)
        {
            player.ApplyUpgrade(upgrade.Id);
        }

        EmitSignal(SignalName.UpgradeSelected, upgrade.Id);
        GD.Print($"[UpgradeManager] Selected: {upgrade.Name}");

        return true;
    }

    public bool TryReroll()
    {
        var cogs = GameManager.Instance.Stats.CogsCollected;

        if (cogs < RerollCost)
        {
            GD.Print($"[UpgradeManager] Cannot reroll: need {RerollCost} cogs, have {cogs}");
            return false;
        }

        // Deduct cost (this is a bit hacky - should have proper currency system)
        GameManager.Instance.Stats.CogsCollected -= RerollCost;

        GenerateOffers();
        EmitSignal(SignalName.UpgradesRerolled);
        GD.Print("[UpgradeManager] Rerolled upgrades");

        return true;
    }

    public int GetRerollCost() => RerollCost;
    public int GetCurrentCogs() => GameManager.Instance?.Stats.CogsCollected ?? 0;
}

/// <summary>
/// Definition of a single upgrade type.
/// </summary>
public record UpgradeDefinition(
    string Name,
    string Id,
    string Description,
    UpgradeRarity Rarity
);

public enum UpgradeRarity
{
    Common,
    Rare,
    Legendary
}
