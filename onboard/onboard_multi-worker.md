# Onboarding: multi-worker

Before starting work on this topic, review these historical lessons:

1. Manager review pattern: workers append to artifacts table (stdout, summaries, diffs) + emit glyphs tagged with job_id/scope. Manager fetches review bundle per job: summary, risks, file list, diff link, errors. Compares bundles, triggers follow-ups.
2. Worker isolation: share Memory OS (common anchors/log) but isolate job state by job_id and scope. Workers stream logs and write results back. Memory glyphs per job (scope=project, job_id=id) for traceability.

Apply these lessons to avoid repeating past mistakes.
