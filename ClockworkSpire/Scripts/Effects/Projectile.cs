using Godot;
using System;
using ClockworkSpire.Enemies;
using ClockworkSpire.Player;

namespace ClockworkSpire.Effects;

/// <summary>
/// Generic projectile that can be used by player or enemies.
/// </summary>
public partial class Projectile : Area2D
{
    [Export] public float Speed { get; set; } = 600f;
    [Export] public int Damage { get; set; } = 1;
    [Export] public float Lifetime { get; set; } = 1.5f;
    [Export] public bool IsEnemyProjectile { get; set; } = false;
    [Export] public int PierceCount { get; set; } = 0;
    [Export] public bool IsCrit { get; set; } = false;

    private Vector2 _velocity = Vector2.Zero;
    private float _lifeTimer = 0f;
    private int _pierceRemaining = 0;

    public override void _Ready()
    {
        // Read metadata if set (for dynamic instantiation)
        if (HasMeta("velocity"))
            _velocity = GetMeta("velocity").AsVector2();
        else
            _velocity = Vector2.Right.Rotated(Rotation) * Speed;

        if (HasMeta("damage"))
            Damage = GetMeta("damage").AsInt32();

        if (HasMeta("is_enemy_projectile"))
            IsEnemyProjectile = GetMeta("is_enemy_projectile").AsBool();

        if (HasMeta("pierce_count"))
            PierceCount = GetMeta("pierce_count").AsInt32();

        if (HasMeta("is_crit"))
            IsCrit = GetMeta("is_crit").AsBool();

        if (HasMeta("lifetime"))
            Lifetime = (float)GetMeta("lifetime").AsDouble();

        _pierceRemaining = PierceCount;
        _lifeTimer = Lifetime;

        // Connect signals
        BodyEntered += OnBodyEntered;
        AreaEntered += OnAreaEntered;

        // Set collision layers based on type
        if (IsEnemyProjectile)
        {
            CollisionLayer = 1 << 3;  // EnemyProjectiles layer
            CollisionMask = 1 << 0;   // Player layer
        }
        else
        {
            CollisionLayer = 1 << 2;  // PlayerProjectiles layer
            CollisionMask = 1 << 1;   // Enemies layer
        }
    }

    public override void _PhysicsProcess(double delta)
    {
        // Move projectile
        GlobalPosition += _velocity * (float)delta;

        // Check lifetime
        _lifeTimer -= (float)delta;
        if (_lifeTimer <= 0)
        {
            QueueFree();
        }
    }

    private void OnBodyEntered(Node2D body)
    {
        if (IsEnemyProjectile)
        {
            // Hit player
            if (body is PlayerController player)
            {
                player.TakeDamage(Damage);
                QueueFree();
            }
        }
        else
        {
            // Hit enemy
            if (body is EnemyBase enemy)
            {
                enemy.TakeDamage(Damage, IsCrit);

                if (_pierceRemaining > 0)
                {
                    _pierceRemaining--;
                }
                else
                {
                    QueueFree();
                }
            }
        }

        // Hit walls
        if (body.IsInGroup("Walls"))
        {
            SpawnImpactEffect();
            QueueFree();
        }
    }

    private void OnAreaEntered(Area2D area)
    {
        // Additional area-based collision handling if needed
    }

    private void SpawnImpactEffect()
    {
        // Create simple impact particle
        var impact = new GpuParticles2D();
        impact.GlobalPosition = GlobalPosition;
        impact.Emitting = true;
        impact.OneShot = true;
        impact.Amount = 8;

        GetTree().CurrentScene.AddChild(impact);

        // Auto-destroy after particles finish
        GetTree().CreateTimer(1.0).Timeout += () =>
        {
            if (IsInstanceValid(impact)) impact.QueueFree();
        };
    }
}
