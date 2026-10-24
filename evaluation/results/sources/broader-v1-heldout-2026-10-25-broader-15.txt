# ADR 0001: Canonical Express service and durable effects

Status: accepted for implementation, cloud operation pending.

The five-framework lab needs a comparable contract while production hardening requires one operational target. Express is canonical. Comparison backends advertise their actual capabilities; missing Go routes are not silently treated as passing. The historical vertical exercise stays separate from the horizontal profile.

Mongo is the durable authority for sessions, idempotency receipts, ingestion jobs and outbox. Majority transactions connect acknowledgement to persisted work. SQS provides at-least-once delivery; leased workers and unique transactional effects tolerate duplicates. Redis is rebuildable public-catalog acceleration. Private authorization never depends on positive Redis cache entries.

ECS/Atlas are the cloud reference because separate tasks, managed data and protected digest promotion meet the scope without adding a Kubernetes control plane. This choice adds provider/network/state/secret operational work. Local Compose supplies repeatable failure tests but cannot establish cloud AZ or PIT recovery.

Tradeoffs: authoritative session reads add database latency; a 30-second catalog TTL permits explicitly bounded staleness; refresh races require reauthentication; Mongo remains a shared bottleneck. Raise pools, replicas or introduce new data infrastructure only after measured capacity identifies a constraint and the cost/consistency model is documented.
