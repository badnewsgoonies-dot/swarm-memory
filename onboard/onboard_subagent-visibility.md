# Onboarding: subagent-visibility

Before starting work on this topic, review these historical lessons:

1. Job ID generation pattern: agentname-$(date +%s)-$RANDOM. Subagents call start at beginning, emit event for each step with progress (0-1), heartbeat every 60s, finish at end with final status.
2. Head integration: head-with-memory.sh calls mem-jobs.sh list --json --limit 5 and injects compact job summary into prompts. Top 5 running jobs + latest step per job gives head omniscient visibility.

Apply these lessons to avoid repeating past mistakes.
