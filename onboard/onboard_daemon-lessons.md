# Onboarding: daemon-lessons

Before starting work on this topic, review these historical lessons:

1. Daemon sub-processes need --llm hybrid flag to avoid Claude CLI OAuth issues. Sub-daemons can get stuck in loops (list_files repeatedly) - use explicit objectives with edit_file instructions. Rate limits at ~15 iterations.

Apply these lessons to avoid repeating past mistakes.
