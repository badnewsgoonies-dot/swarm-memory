using Godot;
using System;

namespace ClockworkSpire.Enemies;

/// <summary>
/// Sprocket Turret - Stationary ranged enemy that shoots at the player.
/// "Mounted sentries that track movement. Patience or speed—choose wisely."
/// </summary>
public partial class SprocketTurret : EnemyBase
{
    [Export] public PackedScene? ProjectileScene { get; set; }
    [Export] public float FireInterval { get; set; } = 2.0f;
    [Export] public float ProjectileSpeed { get; set; } = 300f;
    [Export] public int ProjectileDamage { get; set; } = 1;
    [Export] public float Range { get; set; } = 300f;
    [Export] public float WarningDuration { get; set; } = 0.5f;

    private Node2D? _barrel;
    private float _fireTimer = 0f;
    private bool _isCharging = false;
    private float _chargeTimer = 0f;

    public override void _Ready()
    {
        // Set Turret-specific stats (from game manual)
        MaxHP = 4;
        ContactDamage = 1;  // If player touches it
        MoveSpeed = 0f;  // Stationary

        base._Ready();

        // Get barrel node for rotation
        _barrel = GetNodeOrNull<Node2D>("Barrel");

        // Start with random offset to desync multiple turrets
        _fireTimer = GD.Randf() * FireInterval;
    }

    protected override void ProcessAI(float delta)
    {
        if (Target == null || !IsInstanceValid(Target)) return;

        var distanceToTarget = GlobalPosition.DistanceTo(Target.GlobalPosition);
        var inRange = distanceToTarget <= Range;

        // Always track player with barrel
        TrackTarget();

        // Handle firing logic
        if (inRange)
        {
            if (_isCharging)
            {
                // Charging to fire
                _chargeTimer -= delta;
                if (_chargeTimer <= 0)
                {
                    Fire();
                    _isCharging = false;
                    _fireTimer = FireInterval;
                }
            }
            else
            {
                // Counting down to next shot
                _fireTimer -= delta;
                if (_fireTimer <= 0)
                {
                    StartCharging();
                }
            }
        }
        else
        {
            // Out of range - reset
            _isCharging = false;
            _fireTimer = FireInterval * 0.5f;  // Partial reset
        }
    }

    private void TrackTarget()
    {
        if (Target == null || _barrel == null) return;

        var direction = (Target.GlobalPosition - GlobalPosition).Normalized();
        _barrel.Rotation = direction.Angle();
    }

    private void StartCharging()
    {
        _isCharging = true;
        _chargeTimer = WarningDuration;

        // Visual warning: barrel glows
        if (_barrel != null)
        {
            var tween = CreateTween();
            tween.TweenProperty(_barrel, "modulate", new Color(1.5f, 0.5f, 0.5f), WarningDuration * 0.5f);
        }

        GD.Print("[SprocketTurret] Charging...");
    }

    private void Fire()
    {
        if (Target == null) return;

        // Reset barrel color
        if (_barrel != null)
        {
            _barrel.Modulate = Colors.White;
        }

        // Calculate direction
        var direction = (Target.GlobalPosition - GlobalPosition).Normalized();
        var spawnPos = _barrel?.GlobalPosition ?? GlobalPosition;

        // Create projectile
        if (ProjectileScene != null)
        {
            var projectile = ProjectileScene.Instantiate<Node2D>();
            projectile.GlobalPosition = spawnPos;
            projectile.Rotation = direction.Angle();

            // Set projectile properties
            projectile.SetMeta("velocity", direction * ProjectileSpeed);
            projectile.SetMeta("damage", ProjectileDamage);
            projectile.SetMeta("is_enemy_projectile", true);
            projectile.SetMeta("lifetime", 3.0f);

            GetTree().CurrentScene.AddChild(projectile);
        }
        else
        {
            // Fallback: create simple projectile
            CreateSimpleProjectile(spawnPos, direction);
        }

        GD.Print("[SprocketTurret] Fired!");
    }

    private void CreateSimpleProjectile(Vector2 startPos, Vector2 direction)
    {
        // Simple projectile using Area2D
        var projectile = new Area2D();
        projectile.AddToGroup("EnemyProjectiles");

        var collision = new CollisionShape2D();
        var shape = new CircleShape2D();
        shape.Radius = 4;
        collision.Shape = shape;
        projectile.AddChild(collision);

        // Visual
        var visual = new Sprite2D();
        // visual.Texture = ...
        projectile.AddChild(visual);

        projectile.GlobalPosition = startPos;
        projectile.SetMeta("velocity", direction * ProjectileSpeed);
        projectile.SetMeta("damage", ProjectileDamage);

        GetTree().CurrentScene.AddChild(projectile);

        // Movement script via process
        var lifetime = 3.0f;
        var vel = direction * ProjectileSpeed;

        projectile.SetProcess(true);
        projectile.TreeEntered += () =>
        {
            // Connect area signal
            projectile.BodyEntered += (body) =>
            {
                if (body is Player.PlayerController player)
                {
                    player.TakeDamage(ProjectileDamage);
                    projectile.QueueFree();
                }
            };
        };

        // Simple movement via timer-based update
        GetTree().CreateTimer(lifetime).Timeout += () =>
        {
            if (IsInstanceValid(projectile)) projectile.QueueFree();
        };

        // Move the projectile (we'll do this via a dedicated script in real implementation)
    }
}
