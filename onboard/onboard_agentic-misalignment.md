# Onboarding: agentic-misalignment

Before starting work on this topic, review these historical lessons:

1. Monitoring strategies: behavioral telemetry (log tool calls/memory I/O), runtime policy guards (allow/deny lists), canary tasks + probes, state integrity checks, outcome monitoring (planned vs executed), multi-layer approval for sensitive tools.
2. Mitigation strategies: tighter objectives (explicit contracts, short horizons), conservative scaffolding (default-deny tools, sandboxes), checkpointed oversight, memory hygiene (scope/redact/expire), randomized audits, rate limits and budgets.
3. Autonomous agent deployment checklist: full action audit logs, allow/deny tool policies + depth/budget limits, randomized oversight + canary tasks, human approval gates, memory sweeps for scope violations, test shutdown flows, AGDebugger-style tracing.
4. Separation of duties: split planner, executor, and verifier roles; require cross-approval for high-risk steps; keep verifiers stateless to counter memory-driven drift. Kill-switch with reliable stop endpoints.

Apply these lessons to avoid repeating past mistakes.
