using Godot;
using System;
using ClockworkSpire.Systems;

namespace ClockworkSpire.Player;

/// <summary>
/// Main player controller handling movement, aiming, and combat.
/// Attach to the Player scene root (CharacterBody2D).
/// </summary>
public partial class PlayerController : CharacterBody2D
{
    [Export] public PlayerStats Stats { get; set; } = new();
    [Export] public PackedScene? ProjectileScene { get; set; }
    [Export] public float AccelerationTime { get; set; } = 0.1f;

    // Node references
    private Sprite2D? _sprite;
    private AnimationPlayer? _animPlayer;
    private Node2D? _gunPoint;
    private Area2D? _pickupArea;
    private CollisionShape2D? _pickupCollider;

    // State
    private float _fireCooldown = 0f;
    private float _iFrameTimer = 0f;
    private bool _isInvincible = false;
    private Vector2 _currentVelocity = Vector2.Zero;

    // Constants
    private const float IFRAME_DURATION = 0.5f;
    private const float PROJECTILE_SPEED = 600f;
    private const float PROJECTILE_LIFETIME = 1.5f;

    // Events
    [Signal] public delegate void HealthChangedEventHandler(int current, int max);
    [Signal] public delegate void PlayerDiedEventHandler();
    [Signal] public delegate void DamageTakenEventHandler();

    public override void _Ready()
    {
        // Get node references
        _sprite = GetNodeOrNull<Sprite2D>("Sprite2D");
        _animPlayer = GetNodeOrNull<AnimationPlayer>("AnimationPlayer");
        _gunPoint = GetNodeOrNull<Node2D>("GunPoint");
        _pickupArea = GetNodeOrNull<Area2D>("PickupArea");
        _pickupCollider = _pickupArea?.GetNodeOrNull<CollisionShape2D>("CollisionShape2D");

        // Initialize stats
        Stats.Initialize();
        UpdatePickupRadius();

        // Connect signals
        if (_pickupArea != null)
        {
            _pickupArea.AreaEntered += OnPickupAreaEntered;
        }

        // Register with GameManager
        GameManager.Instance.CurrentPlayer = this;

        EmitSignal(SignalName.HealthChanged, Stats.CurrentHP, Stats.MaxHP);
        GD.Print("[Player] Ready");
    }

    public override void _PhysicsProcess(double delta)
    {
        HandleMovement((float)delta);
        HandleAiming();
        HandleShooting((float)delta);
        HandleIFrames((float)delta);
    }

    private void HandleMovement(float delta)
    {
        // Get input direction
        var inputDir = Input.GetVector("move_left", "move_right", "move_up", "move_down");

        // Calculate target velocity
        var targetVelocity = inputDir * Stats.EffectiveMoveSpeed;

        // Smooth acceleration/deceleration
        _currentVelocity = _currentVelocity.Lerp(targetVelocity, 1.0f - Mathf.Exp(-delta / AccelerationTime));

        // Apply movement
        Velocity = _currentVelocity;
        MoveAndSlide();

        // Update animation
        if (_animPlayer != null)
        {
            if (inputDir.Length() > 0.1f)
            {
                _animPlayer.Play("walk");
            }
            else
            {
                _animPlayer.Play("idle");
            }
        }
    }

    private void HandleAiming()
    {
        // Get mouse position in world space
        var mousePos = GetGlobalMousePosition();
        var direction = (mousePos - GlobalPosition).Normalized();

        // Flip sprite based on aim direction
        if (_sprite != null)
        {
            _sprite.FlipH = direction.X < 0;
        }

        // Position gun point
        if (_gunPoint != null)
        {
            // Rotate gun point toward mouse
            _gunPoint.Rotation = direction.Angle();
        }
    }

    private void HandleShooting(float delta)
    {
        // Decrease cooldown
        if (_fireCooldown > 0)
        {
            _fireCooldown -= delta;
        }

        // Check for fire input (hold to auto-fire)
        if (Input.IsActionPressed("fire") && _fireCooldown <= 0)
        {
            Fire();
            _fireCooldown = 1.0f / Stats.EffectiveFireRate;
        }
    }

    private void Fire()
    {
        if (ProjectileScene == null)
        {
            GD.PrintErr("[Player] No projectile scene assigned!");
            return;
        }

        // Create projectile
        var projectile = ProjectileScene.Instantiate<Node2D>();

        // Position at gun point
        var spawnPos = _gunPoint?.GlobalPosition ?? GlobalPosition;
        var direction = (GetGlobalMousePosition() - GlobalPosition).Normalized();

        // Set projectile properties (assuming it has a script with these methods/properties)
        projectile.GlobalPosition = spawnPos;
        projectile.Rotation = direction.Angle();

        // Set velocity via metadata or direct property
        projectile.SetMeta("velocity", direction * PROJECTILE_SPEED);
        projectile.SetMeta("damage", Stats.EffectiveDamage);
        projectile.SetMeta("pierce_count", Stats.HasPiercingRounds ? Stats.PierceCount : 0);
        projectile.SetMeta("is_crit", GD.Randf() < Stats.CritChance);
        projectile.SetMeta("lifetime", PROJECTILE_LIFETIME);

        // Add to scene tree
        GetTree().CurrentScene.AddChild(projectile);
    }

    private void HandleIFrames(float delta)
    {
        if (!_isInvincible) return;

        _iFrameTimer -= delta;

        // Flash effect
        if (_sprite != null)
        {
            _sprite.Visible = ((int)(_iFrameTimer * 10) % 2) == 0;
        }

        if (_iFrameTimer <= 0)
        {
            _isInvincible = false;
            if (_sprite != null) _sprite.Visible = true;
        }
    }

    public void TakeDamage(int amount)
    {
        if (_isInvincible) return;

        bool died = Stats.TakeDamage(amount);
        EmitSignal(SignalName.HealthChanged, Stats.CurrentHP, Stats.MaxHP);
        EmitSignal(SignalName.DamageTaken);

        GD.Print($"[Player] Took {amount} damage. HP: {Stats.CurrentHP}/{Stats.MaxHP}");

        if (died)
        {
            Die();
        }
        else
        {
            // Start invincibility frames
            _isInvincible = true;
            _iFrameTimer = IFRAME_DURATION;

            // Play hurt animation
            if (_animPlayer != null && _animPlayer.HasAnimation("hurt"))
            {
                _animPlayer.Play("hurt");
            }
        }
    }

    public void Heal(int amount)
    {
        Stats.Heal(amount);
        EmitSignal(SignalName.HealthChanged, Stats.CurrentHP, Stats.MaxHP);
        GD.Print($"[Player] Healed {amount}. HP: {Stats.CurrentHP}/{Stats.MaxHP}");
    }

    private void Die()
    {
        GD.Print("[Player] Died!");
        EmitSignal(SignalName.PlayerDied);

        // Play death animation if available
        if (_animPlayer != null && _animPlayer.HasAnimation("death"))
        {
            _animPlayer.Play("death");
        }

        // Disable player
        SetPhysicsProcess(false);
        if (_pickupArea != null) _pickupArea.SetDeferred("monitoring", false);

        // Notify game manager
        GameManager.Instance.EndRun(victory: false);
    }

    private void OnPickupAreaEntered(Area2D area)
    {
        // Handle pickups
        if (area.IsInGroup("Pickups"))
        {
            // Check pickup type via metadata
            var pickupType = area.GetMeta("type", "cog").AsString();

            switch (pickupType)
            {
                case "cog":
                    GameManager.Instance.Stats.CogsCollected++;
                    GD.Print("[Player] Collected cog");
                    break;
                case "health":
                    Heal(1);
                    break;
            }

            // Destroy pickup
            area.QueueFree();
        }
    }

    public void UpdatePickupRadius()
    {
        if (_pickupCollider?.Shape is CircleShape2D circle)
        {
            circle.Radius = Stats.EffectivePickupRadius;
        }
    }

    public void ApplyUpgrade(string upgradeName)
    {
        Stats.ApplyUpgrade(upgradeName);
        UpdatePickupRadius();
        EmitSignal(SignalName.HealthChanged, Stats.CurrentHP, Stats.MaxHP);
    }
}
