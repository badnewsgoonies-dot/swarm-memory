# Onboarding: daemon

Before starting work on this topic, review these historical lessons:

1. Daemon orchestration with OpenAI models struggles with complex porting tasks. Issues: 1) Orchestrator skips IMPLEMENT phase and jumps to AUDIT 2) Sub-daemons loop reading same files without making edits 3) Can't easily access files outside repo_root. Claude CLI would work better but has expired auth.

Apply these lessons to avoid repeating past mistakes.
