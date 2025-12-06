# Onboarding: memory-safety

Before starting work on this topic, review these historical lessons:

1. Anti-self-reinforcement: agent-authored rules default to priority=rule and need user confirmation to escalate. Block chains where origin=agent rules create new rules without user/system confirmation.
2. Conflict handling: before writing new rules, search for opposing t=r in same topic. Emit t=n with supersedes=<id> instead of duplicating. Apply stronger decay to agent-authored rules; require renewal to persist.

Apply these lessons to avoid repeating past mistakes.
