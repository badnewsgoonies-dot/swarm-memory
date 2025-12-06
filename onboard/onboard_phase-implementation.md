# Onboarding: phase-implementation

Before starting work on this topic, review these historical lessons:

1. Guardrails for manager/worker loop: MAX_STEPS cap, tool gating (only whitelisted tools, manager specifies name+params), memory injection before each call (task context + recent lessons).
2. Role separation: Manager owns goal, breaks into steps, assigns to worker, reviews outputs, records decisions (choice=decision, role=manager). Worker executes subtasks, records ATTEMPT/RESULT, references lessons, no final authority.

Apply these lessons to avoid repeating past mistakes.
