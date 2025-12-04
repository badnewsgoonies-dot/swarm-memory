using Godot;
using System;

namespace ClockworkSpire.Enemies;

/// <summary>
/// Ticker - Basic melee enemy that chases the player.
/// "Small, fast, and numerous. Tickers swarm intruders with reckless abandon."
/// </summary>
public partial class Ticker : EnemyBase
{
    [Export] public float StunDuration { get; set; } = 0.3f;

    private float _stunTimer = 0f;
    private bool _isStunned = false;

    public override void _Ready()
    {
        // Set Ticker-specific stats (from game manual)
        MaxHP = 2;
        ContactDamage = 1;
        MoveSpeed = 140f;

        base._Ready();
    }

    protected override void ProcessAI(float delta)
    {
        // Handle stun
        if (_isStunned)
        {
            _stunTimer -= delta;
            if (_stunTimer <= 0)
            {
                _isStunned = false;
            }
            Velocity = Vector2.Zero;
            MoveAndSlide();
            return;
        }

        // Chase player
        if (Target != null && IsInstanceValid(Target))
        {
            MoveToward(Target.GlobalPosition, MoveSpeed);
            FaceToward(Target.GlobalPosition);

            // Play walk animation
            if (AnimPlayer != null && AnimPlayer.HasAnimation("walk"))
            {
                if (!AnimPlayer.IsPlaying() || AnimPlayer.CurrentAnimation != "walk")
                {
                    AnimPlayer.Play("walk");
                }
            }
        }
        else
        {
            Velocity = Vector2.Zero;
            MoveAndSlide();

            // Play idle animation
            if (AnimPlayer != null && AnimPlayer.HasAnimation("idle"))
            {
                AnimPlayer.Play("idle");
            }
        }
    }

    /// <summary>
    /// Called when Ticker hits the player - brief stun after contact.
    /// </summary>
    public void OnHitPlayer()
    {
        _isStunned = true;
        _stunTimer = StunDuration;
    }

    protected override void Die()
    {
        // Ticker-specific death effect: gear explosion
        SpawnGearExplosion();
        base.Die();
    }

    private void SpawnGearExplosion()
    {
        // Create particle effect (simple visual feedback)
        // In full implementation, use GPUParticles2D

        // Create a few small "gear" sprites that fly outward
        for (int i = 0; i < 4; i++)
        {
            var gear = new Sprite2D();
            // gear.Texture would be set to a gear sprite
            gear.GlobalPosition = GlobalPosition;
            gear.Scale = new Vector2(0.5f, 0.5f);

            GetTree().CurrentScene.AddChild(gear);

            // Animate outward
            var angle = i * Mathf.Pi / 2 + GD.Randf() * 0.5f;
            var direction = new Vector2(Mathf.Cos(angle), Mathf.Sin(angle));
            var targetPos = GlobalPosition + direction * 30;

            var tween = gear.CreateTween();
            tween.TweenProperty(gear, "global_position", targetPos, 0.3f);
            tween.Parallel().TweenProperty(gear, "modulate:a", 0f, 0.3f);
            tween.Parallel().TweenProperty(gear, "rotation", gear.Rotation + Mathf.Pi * 2, 0.3f);
            tween.TweenCallback(Callable.From(gear.QueueFree));
        }
    }
}
