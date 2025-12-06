# Onboarding: autonomy-loop

Before starting work on this topic, review these historical lessons:

1. Safety guards for daemon: max_iters/wall-clock timeout, human checkpoints for critical steps, approval gates for data writes/unsafe ops, auto-exit on no GOAL/no progress/repeated BLOCKED/search failures.
2. Daemon operational requirements: log every transition and glyph ID to trace decisions, refuse to proceed if log write fails. Rate limits with backoff between iterations, budget caps for tool calls.
3. Manager/Worker separation: Manager plans from GOAL, creates TODOs, reviews SUMMARYs, emits FEEDBACK. Worker executes TODOs, writes SUMMARY or ISSUE. Manager can expand TODOs during REVIEW phase if needed.

Apply these lessons to avoid repeating past mistakes.
