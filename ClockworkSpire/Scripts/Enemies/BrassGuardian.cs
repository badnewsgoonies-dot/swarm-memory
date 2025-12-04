using Godot;
using System;
using ClockworkSpire.Systems;

namespace ClockworkSpire.Enemies;

/// <summary>
/// Brass Guardian - Boss enemy with two phases.
/// "The floor's keeper. Ancient. Powerful. Patient."
/// </summary>
public partial class BrassGuardian : EnemyBase
{
    [Export] public PackedScene? TickerScene { get; set; }
    [Export] public PackedScene? ShockwaveScene { get; set; }

    // Phase 1 settings
    [Export] public float Phase1Speed { get; set; } = 60f;
    [Export] public float Phase1ShockwaveInterval { get; set; } = 3.0f;

    // Phase 2 settings
    [Export] public float Phase2Speed { get; set; } = 100f;
    [Export] public float Phase2ShockwaveInterval { get; set; } = 2.0f;
    [Export] public float Phase2SpawnInterval { get; set; } = 5.0f;
    [Export] public int Phase2SpawnCount { get; set; } = 2;

    // Phase threshold
    [Export] public int Phase2HPThreshold { get; set; } = 15;

    // State
    private int _currentPhase = 1;
    private float _shockwaveTimer = 0f;
    private float _spawnTimer = 0f;
    private bool _isTransitioning = false;
    private float _transitionTimer = 0f;

    // Events
    [Signal] public delegate void PhaseChangedEventHandler(int phase);
    [Signal] public delegate void ShockwaveCreatedEventHandler();

    public override void _Ready()
    {
        // Set Boss-specific stats (from game manual)
        MaxHP = 25;
        ContactDamage = 2;
        MoveSpeed = Phase1Speed;

        base._Ready();

        _shockwaveTimer = Phase1ShockwaveInterval;
        _spawnTimer = Phase2SpawnInterval;
    }

    protected override void ProcessAI(float delta)
    {
        // Handle phase transition
        if (_isTransitioning)
        {
            _transitionTimer -= delta;
            if (_transitionTimer <= 0)
            {
                _isTransitioning = false;
                FinishPhaseTransition();
            }
            return;
        }

        // Check for phase change
        if (_currentPhase == 1 && CurrentHP <= Phase2HPThreshold)
        {
            StartPhaseTransition();
            return;
        }

        // Phase-specific behavior
        if (_currentPhase == 1)
        {
            ProcessPhase1(delta);
        }
        else
        {
            ProcessPhase2(delta);
        }
    }

    private void ProcessPhase1(float delta)
    {
        // Slow chase
        if (Target != null && IsInstanceValid(Target))
        {
            MoveToward(Target.GlobalPosition, Phase1Speed);
            FaceToward(Target.GlobalPosition);
        }

        // Shockwave attack
        _shockwaveTimer -= delta;
        if (_shockwaveTimer <= 0)
        {
            CreateShockwave();
            _shockwaveTimer = Phase1ShockwaveInterval;
        }
    }

    private void ProcessPhase2(float delta)
    {
        // Faster chase
        if (Target != null && IsInstanceValid(Target))
        {
            MoveToward(Target.GlobalPosition, Phase2Speed);
            FaceToward(Target.GlobalPosition);
        }

        // Faster shockwaves
        _shockwaveTimer -= delta;
        if (_shockwaveTimer <= 0)
        {
            CreateShockwave();
            _shockwaveTimer = Phase2ShockwaveInterval;
        }

        // Spawn adds
        _spawnTimer -= delta;
        if (_spawnTimer <= 0)
        {
            SpawnTickers();
            _spawnTimer = Phase2SpawnInterval;
        }
    }

    private void StartPhaseTransition()
    {
        _isTransitioning = true;
        _transitionTimer = 1.0f;
        _currentPhase = 2;

        // Stop movement
        Velocity = Vector2.Zero;

        // Visual feedback
        GD.Print("[BrassGuardian] Entering Phase 2!");

        // Screen shake effect (emit signal for camera)
        // TODO: Implement screen shake

        // Boss "roar" visual - flash red and enlarge briefly
        if (Sprite != null)
        {
            var tween = CreateTween();
            tween.TweenProperty(Sprite, "modulate", new Color(1.5f, 0.3f, 0.3f), 0.3f);
            tween.TweenProperty(Sprite, "scale", Sprite.Scale * 1.1f, 0.2f);
            tween.TweenProperty(Sprite, "scale", Sprite.Scale, 0.2f);
        }

        EmitSignal(SignalName.PhaseChanged, 2);
    }

    private void FinishPhaseTransition()
    {
        // Reset visuals
        if (Sprite != null)
        {
            // Keep a slight red tint for Phase 2
            Sprite.Modulate = new Color(1.2f, 0.9f, 0.9f);
        }

        // Enable eye glow effect if available
        var eyes = GetNodeOrNull<Node2D>("Eyes");
        if (eyes != null)
        {
            eyes.Visible = true;
            // Animate glow
            var eyeTween = CreateTween();
            eyeTween.SetLoops();
            eyeTween.TweenProperty(eyes, "modulate:a", 0.5f, 0.5f);
            eyeTween.TweenProperty(eyes, "modulate:a", 1.0f, 0.5f);
        }

        GD.Print("[BrassGuardian] Phase 2 active - faster attacks, spawning adds");
    }

    private void CreateShockwave()
    {
        GD.Print("[BrassGuardian] Creating shockwave!");

        EmitSignal(SignalName.ShockwaveCreated);

        if (ShockwaveScene != null)
        {
            var shockwave = ShockwaveScene.Instantiate<Node2D>();
            shockwave.GlobalPosition = GlobalPosition;
            shockwave.SetMeta("damage", 2);
            GetTree().CurrentScene.AddChild(shockwave);
        }
        else
        {
            // Create simple shockwave
            CreateSimpleShockwave();
        }

        // Play pound animation
        if (AnimPlayer != null && AnimPlayer.HasAnimation("pound"))
        {
            AnimPlayer.Play("pound");
        }
    }

    private void CreateSimpleShockwave()
    {
        // Create expanding ring shockwave
        var shockwave = new Area2D();
        shockwave.AddToGroup("Shockwaves");
        shockwave.GlobalPosition = GlobalPosition;

        var collision = new CollisionShape2D();
        var shape = new CircleShape2D();
        shape.Radius = 10;  // Start small
        collision.Shape = shape;
        shockwave.AddChild(collision);

        // Visual ring (using a simple colored sprite or drawing)
        var visual = new Node2D();
        shockwave.AddChild(visual);

        GetTree().CurrentScene.AddChild(shockwave);

        // Connect to hit player
        shockwave.BodyEntered += (body) =>
        {
            if (body is Player.PlayerController player)
            {
                player.TakeDamage(2);
            }
        };

        // Animate expansion
        var maxRadius = 150f;
        var duration = 0.8f;

        var tween = shockwave.CreateTween();
        tween.TweenMethod(
            Callable.From((float radius) =>
            {
                if (IsInstanceValid(shockwave) && collision.Shape is CircleShape2D circle)
                {
                    circle.Radius = radius;
                }
            }),
            10f, maxRadius, duration
        );
        tween.TweenCallback(Callable.From(shockwave.QueueFree));
    }

    private void SpawnTickers()
    {
        GD.Print($"[BrassGuardian] Spawning {Phase2SpawnCount} Tickers!");

        for (int i = 0; i < Phase2SpawnCount; i++)
        {
            if (TickerScene != null)
            {
                var ticker = TickerScene.Instantiate<EnemyBase>();

                // Spawn at offset from boss
                var angle = GD.Randf() * Mathf.Tau;
                var offset = new Vector2(Mathf.Cos(angle), Mathf.Sin(angle)) * 50;
                ticker.GlobalPosition = GlobalPosition + offset;

                GetTree().CurrentScene.AddChild(ticker);

                // Register with room manager
                var roomManager = GetTree().CurrentScene.GetNodeOrNull<RoomManager>("RoomManager");
                roomManager?.RegisterEnemy(ticker);

                // Activate after brief delay
                GetTree().CreateTimer(0.5).Timeout += ticker.Activate;
            }
        }
    }

    protected override void Die()
    {
        GD.Print("[BrassGuardian] DEFEATED!");

        // Epic death sequence
        if (Sprite != null)
        {
            var tween = CreateTween();

            // Flash rapidly
            for (int i = 0; i < 5; i++)
            {
                tween.TweenProperty(Sprite, "modulate", Colors.White, 0.1f);
                tween.TweenProperty(Sprite, "modulate", new Color(2, 2, 2), 0.1f);
            }

            // Expand and fade
            tween.TweenProperty(Sprite, "scale", Sprite.Scale * 1.5f, 0.5f);
            tween.Parallel().TweenProperty(Sprite, "modulate:a", 0f, 0.5f);
        }

        // Wait for animation, then signal victory
        GetTree().CreateTimer(1.5).Timeout += () =>
        {
            // Notify game manager
            GameManager.Instance.EndRun(victory: true);
            QueueFree();
        };

        // Still call base for room manager notification
        IsDead = true;
        IsActive = false;
        SetPhysicsProcess(false);

        var roomManager = GetTree().CurrentScene.GetNodeOrNull<RoomManager>("RoomManager");
        roomManager?.OnEnemyDied(this);

        EmitSignal(SignalName.Died, this);
    }

    public int GetCurrentPhase() => _currentPhase;
    public float GetHPPercent() => (float)CurrentHP / MaxHP;
}
