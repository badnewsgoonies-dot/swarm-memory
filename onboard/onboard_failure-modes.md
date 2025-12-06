# Onboarding: failure-modes

Before starting work on this topic, review these historical lessons:

1. Memory bloat mitigation: enforce dedup/near-dup pruning, time-weighted ranking, cap per-topic memory, compress older chunks. Detection: rising hit count with falling precision, high similarity clusters.
2. Hallucinated memory prevention: two-step write (draft→verify→commit), require citations/links for writes, mark assertions with confidence and source. Reject speculative modal language. Supersede-not-edit to correct errors.
3. Multi-agent confusion prevention: namespaced memories per agent/task, role-locked tool access, explicit handoff records with commit messages summarizing intent and ownership. Coordinator/arbiter step for disambiguation.
4. Temporal confusion prevention: time-aware ranking/decay, versioned entities, expiry/TTL for transient facts, periodic refresh of long-lived summaries. Store validity intervals, prefer latest-version pointers.

Apply these lessons to avoid repeating past mistakes.
