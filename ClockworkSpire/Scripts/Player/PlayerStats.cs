using Godot;
using System;

namespace ClockworkSpire.Player;

/// <summary>
/// Manages player statistics that can be modified by upgrades.
/// </summary>
public partial class PlayerStats : Resource
{
    // Base stats (from game manual)
    [Export] public int MaxHP { get; set; } = 6;
    [Export] public float MoveSpeed { get; set; } = 220f;
    [Export] public float FireRate { get; set; } = 4.0f;  // Shots per second
    [Export] public int Damage { get; set; } = 1;
    [Export] public float CritChance { get; set; } = 0.05f;  // 5%
    [Export] public float PickupRadius { get; set; } = 48f;

    // Computed properties
    public float FireCooldown => 1.0f / FireRate;

    // Current state
    public int CurrentHP { get; set; }

    // Upgrade multipliers (applied on top of base stats)
    public float FireRateMultiplier { get; set; } = 1.0f;
    public float MoveSpeedMultiplier { get; set; } = 1.0f;
    public float DamageMultiplier { get; set; } = 1.0f;
    public float PickupRadiusMultiplier { get; set; } = 1.0f;

    // Upgrade flags
    public bool HasPiercingRounds { get; set; } = false;
    public int PierceCount { get; set; } = 0;

    // Effective stats (base * multiplier)
    public float EffectiveMoveSpeed => MoveSpeed * MoveSpeedMultiplier;
    public float EffectiveFireRate => FireRate * FireRateMultiplier;
    public int EffectiveDamage => (int)(Damage * DamageMultiplier);
    public float EffectivePickupRadius => PickupRadius * PickupRadiusMultiplier;

    public void Initialize()
    {
        CurrentHP = MaxHP;
        ResetMultipliers();
    }

    public void ResetMultipliers()
    {
        FireRateMultiplier = 1.0f;
        MoveSpeedMultiplier = 1.0f;
        DamageMultiplier = 1.0f;
        PickupRadiusMultiplier = 1.0f;
        HasPiercingRounds = false;
        PierceCount = 0;
    }

    public bool TakeDamage(int amount)
    {
        CurrentHP = Math.Max(0, CurrentHP - amount);
        GameManager.Instance.Stats.DamageTaken += amount;
        return CurrentHP <= 0;
    }

    public void Heal(int amount)
    {
        CurrentHP = Math.Min(MaxHP, CurrentHP + amount);
    }

    public void IncreaseMaxHP(int amount)
    {
        MaxHP += amount;
        CurrentHP += amount;  // Also heal when max HP increases
    }

    /// <summary>
    /// Apply an upgrade by name.
    /// </summary>
    public void ApplyUpgrade(string upgradeName)
    {
        switch (upgradeName.ToLower())
        {
            case "overclock":
                FireRateMultiplier += 0.2f;  // +20% fire rate
                break;
            case "reinforced_frame":
                IncreaseMaxHP(2);
                break;
            case "piercing_rounds":
                HasPiercingRounds = true;
                PierceCount = 1;
                break;
            case "quick_gears":
                MoveSpeedMultiplier += 0.15f;  // +15% speed
                break;
            case "scrap_magnet":
                PickupRadiusMultiplier += 0.5f;  // +50% pickup radius
                break;
            case "critical_tuning":
                CritChance += 0.1f;  // +10% crit
                break;
            default:
                GD.PrintErr($"[PlayerStats] Unknown upgrade: {upgradeName}");
                break;
        }

        GameManager.Instance.Stats.UpgradesCollected++;
        GD.Print($"[PlayerStats] Applied upgrade: {upgradeName}");
    }
}
