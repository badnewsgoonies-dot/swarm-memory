# Onboarding: multi-chat-sync

Before starting work on this topic, review these historical lessons:

1. Pull-first is simplest for multi-chat: each chat pulls recent anchors since cursor before building prompt. Pure push is brittle with SQLite. Use pull + optional invalidation signal.

Apply these lessons to avoid repeating past mistakes.
