using Godot;
using System;
using ClockworkSpire.Systems;

namespace ClockworkSpire.Enemies;

/// <summary>
/// Base class for all enemy constructs.
/// </summary>
public abstract partial class EnemyBase : CharacterBody2D
{
    [Export] public int MaxHP { get; set; } = 3;
    [Export] public int ContactDamage { get; set; } = 1;
    [Export] public float MoveSpeed { get; set; } = 100f;

    public int CurrentHP { get; protected set; }
    public bool IsActive { get; protected set; } = false;
    public bool IsDead { get; protected set; } = false;

    // Node references
    protected Sprite2D? Sprite;
    protected AnimationPlayer? AnimPlayer;
    protected Area2D? HitBox;

    // Target (usually the player)
    protected Node2D? Target;

    // Events
    [Signal] public delegate void DiedEventHandler(Node2D enemy);
    [Signal] public delegate void DamageTakenEventHandler(int remaining);

    // Drop chances
    protected const float COG_DROP_CHANCE = 1.0f;  // Always drop cog
    protected const float HEALTH_DROP_CHANCE = 0.15f;  // 15% chance

    public override void _Ready()
    {
        CurrentHP = MaxHP;

        // Get node references
        Sprite = GetNodeOrNull<Sprite2D>("Sprite2D");
        AnimPlayer = GetNodeOrNull<AnimationPlayer>("AnimationPlayer");
        HitBox = GetNodeOrNull<Area2D>("HitBox");

        // Find player
        Target = GameManager.Instance.CurrentPlayer;

        // Connect to hit detection
        if (HitBox != null)
        {
            HitBox.BodyEntered += OnBodyEnteredHitBox;
        }

        // Register with room manager (if available)
        var roomManager = GetTree().CurrentScene.GetNodeOrNull<RoomManager>("RoomManager");
        roomManager?.RegisterEnemy(this);

        // Start inactive (activated after spawn delay)
        SetPhysicsProcess(false);

        GD.Print($"[{GetType().Name}] Spawned");
    }

    /// <summary>
    /// Called to activate the enemy after spawn delay.
    /// </summary>
    public virtual void Activate()
    {
        IsActive = true;
        SetPhysicsProcess(true);
        GD.Print($"[{GetType().Name}] Activated");
    }

    public override void _PhysicsProcess(double delta)
    {
        if (!IsActive || IsDead) return;

        // Refresh target reference if needed
        Target ??= GameManager.Instance.CurrentPlayer;

        ProcessAI((float)delta);
    }

    /// <summary>
    /// Override in derived classes to implement enemy-specific behavior.
    /// </summary>
    protected abstract void ProcessAI(float delta);

    public virtual void TakeDamage(int amount, bool isCrit = false)
    {
        if (IsDead) return;

        int actualDamage = isCrit ? amount * 2 : amount;
        CurrentHP -= actualDamage;

        EmitSignal(SignalName.DamageTaken, CurrentHP);
        GD.Print($"[{GetType().Name}] Took {actualDamage} damage (crit: {isCrit}). HP: {CurrentHP}/{MaxHP}");

        // Flash effect
        FlashDamage();

        if (CurrentHP <= 0)
        {
            Die();
        }
    }

    protected virtual void FlashDamage()
    {
        if (Sprite == null) return;

        // Simple flash: modulate to white briefly
        var tween = CreateTween();
        tween.TweenProperty(Sprite, "modulate", new Color(2, 2, 2), 0.05f);
        tween.TweenProperty(Sprite, "modulate", Colors.White, 0.05f);
    }

    protected virtual void Die()
    {
        IsDead = true;
        IsActive = false;
        SetPhysicsProcess(false);

        GD.Print($"[{GetType().Name}] Died");

        // Drop loot
        SpawnDrops();

        // Notify room manager
        var roomManager = GetTree().CurrentScene.GetNodeOrNull<RoomManager>("RoomManager");
        roomManager?.OnEnemyDied(this);

        // Emit signal
        EmitSignal(SignalName.Died, this);

        // Play death animation or destroy
        if (AnimPlayer != null && AnimPlayer.HasAnimation("death"))
        {
            AnimPlayer.Play("death");
            AnimPlayer.AnimationFinished += (_) => QueueFree();
        }
        else
        {
            // Simple destroy with fade
            var tween = CreateTween();
            tween.TweenProperty(Sprite, "modulate:a", 0f, 0.2f);
            tween.TweenCallback(Callable.From(QueueFree));
        }
    }

    protected virtual void SpawnDrops()
    {
        // Always drop cog
        if (GD.Randf() < COG_DROP_CHANCE)
        {
            SpawnPickup("cog");
        }

        // Chance to drop health
        if (GD.Randf() < HEALTH_DROP_CHANCE)
        {
            SpawnPickup("health");
        }
    }

    protected void SpawnPickup(string type)
    {
        // Create a simple pickup area
        var pickup = new Area2D();
        pickup.AddToGroup("Pickups");
        pickup.SetMeta("type", type);

        var collision = new CollisionShape2D();
        var shape = new CircleShape2D();
        shape.Radius = 8;
        collision.Shape = shape;
        pickup.AddChild(collision);

        // Simple visual (colored circle for now)
        var visual = new Sprite2D();
        // In a real implementation, load actual pickup sprites
        pickup.AddChild(visual);

        pickup.GlobalPosition = GlobalPosition + new Vector2(GD.Randf() * 16 - 8, GD.Randf() * 16 - 8);
        GetTree().CurrentScene.AddChild(pickup);

        // Auto-destroy after time
        GetTree().CreateTimer(30.0).Timeout += () => { if (IsInstanceValid(pickup)) pickup.QueueFree(); };
    }

    private void OnBodyEnteredHitBox(Node2D body)
    {
        if (!IsActive || IsDead) return;

        // Deal contact damage to player
        if (body is Player.PlayerController player)
        {
            player.TakeDamage(ContactDamage);
        }
    }

    /// <summary>
    /// Helper to move toward a target position.
    /// </summary>
    protected void MoveToward(Vector2 targetPos, float speed)
    {
        var direction = (targetPos - GlobalPosition).Normalized();
        Velocity = direction * speed;
        MoveAndSlide();
    }

    /// <summary>
    /// Helper to face toward a target position.
    /// </summary>
    protected void FaceToward(Vector2 targetPos)
    {
        if (Sprite != null)
        {
            Sprite.FlipH = targetPos.X < GlobalPosition.X;
        }
    }
}
