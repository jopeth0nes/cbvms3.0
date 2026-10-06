---
name: cbvms-postgres
description: "Work on actual PostgreSQL schemas, indexes, query plans, pooling or concurrency in CBVMS. Do not activate for current SQLite queries or generic Python persistence."
---

# PostgreSQL only

CBVMS currently uses SQLite; this guidance applies only to an intentional PostgreSQL implementation. Confirm the actual driver/server version and requested workload before recommending syntax or infrastructure.

Preserve student text identifiers, resource ownership, unique event keys and foreign keys across violations, suspensions, attendance, appeals, sightings and notifications. Use driver parameters and least-privilege application roles; do not assume row-level security exists or replaces application ownership checks.

Derive indexes from real filters/joins and plans. For dashboards, confirm course, year level and college/department joins do not multiply records. Use `EXPLAIN`; `EXPLAIN ANALYZE` executes the query, so use disposable data for writes and expensive work. Report observed plans/timings, not assumed gains.

Bound pool sizes, transaction duration, lock waits and retries. Verify concurrent deduplication with constraints; preserve idempotency on retry. Use timezone-aware timestamps deliberately and test local-day reporting. Document backup/restore and recovery before production changes. Load migration guidance only when schema/data conversion is part of the task.
