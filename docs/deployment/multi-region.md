# Multi-Region Deployment Contract (Item 83)

## Status

**v1: active-passive only.** Active-active multi-region is explicitly out of
scope for v1 and is documented here so operators planning a deployment
discover the limits before they hit them in production.

## Recommended topology

A primary region runs the framework against a primary database; secondary
regions run read-only replicas (Item 54) for read-heavy workloads. Failover
is operator-driven (DNS or load-balancer cutover) with the RTO/RPO targets
documented in Item 79's backup contract.

Item 36's residency primitive routes user traffic to the in-jurisdiction
region's primary; cross-region writes go to the user's home region.

## Why active-active is deferred

The v1 framework primitives are designed for single-region authoritative
state. Mixing active-active deployment with v1 risks **silent over-counting
on quota, duplicate sends from the outbox, and routing inconsistency on
stickiness** — none of which produce immediate errors, but all of which
produce financial or correctness drift.

The primitives that would need changes for v2 active-active:

- **Item 19 — Quota.** `Quota` decrement uses local-DB transactions. For
  active-active, this needs cross-region consensus (Raft-backed quota
  ledger, or a single-region-authoritative quota leader with a documented
  failover protocol) or per-region quota partitioning with explicit
  reconciliation windows.

- **Item 35 — Outbox.** A single global outbox drainer becomes the
  bottleneck and a split-brain risk in active-active. v2 needs per-region
  outbox sharding by tenant key, with each region authoritatively draining
  its own shard. Cross-region failover must hand the orphaned shard to a
  new owner without double-draining.

- **Item 51 — Sticky-session routing.** The current process-local sticky
  cache cannot federate across regions. v2 needs either a global Redis
  with cross-region replication (acceptable correctness drift documented),
  or session-affinity enforcement at the load-balancer layer
  (region-pinned cookies / consistent-hash routing).

- **Item 32 — Credential vault.** Credential cache invalidation is
  process-local. v2 needs cross-region cache-bust on rotation
  (pub/sub-driven, OpenBao watch endpoints, or a TTL short enough that
  the staleness window is acceptable to the deployment's compliance
  posture).

- **Item 69 — DistributedCounter.** Counters operate against a regional
  Redis with documented eventual-consistency semantics, or against a
  globally-replicated Postgres with explicit conflict-resolution. The
  v1 single-region implementation is exact; the v2 multi-region
  implementation is approximate and the docs must enumerate which
  countertypes are safe to be approximate.

- **Item 54 — Read replicas.** Documented as same-region for v1.
  Cross-region replicas in v1 risk read-after-write inconsistency
  visible to the same user across requests routed to different regions.
  v2 needs sticky read-region per session or read-after-write fencing.

## Operator checklist (v1 active-passive)

1. Identify a primary region per tenant (Item 36).
2. Provision read replicas in each secondary region for read-heavy
   workloads (Item 54).
3. Provision a separate database in each region for primary-failover use,
   replicated from the primary on a documented RPO (Item 79).
4. Configure DNS / load-balancer with weighted routing — primary region
   gets 100% writes; secondary regions get reads only.
5. Document the failover procedure and RTO target. Operator-driven cutover
   only; the framework does not auto-failover.
6. Confirm the outbox (Item 35) drains exclusively from the primary;
   secondary regions must NOT have outbox drainers running against the
   replicated DB.

## Acceptance criteria

This document satisfies Item 83 when:

- Active-passive is explicitly identified as the v1-supported topology.
- Active-active is explicitly identified as out-of-scope for v1.
- The primitives that would change for v2 are enumerated with
  rationale per primitive.
- Operators planning multi-region deployments find the limits documented
  before they hit them in production.

## Related items

- Item 19 — Quota
- Item 32 — Credential vault
- Item 35 — Outbox
- Item 36 — Data residency
- Item 51 — Sticky-session routing
- Item 54 — Read-replica routing
- Item 69 — Distributed counter
- Item 79 — Backup, restore, point-in-time recovery
