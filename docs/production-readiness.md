# Production Readiness

This repository is a development prototype. The items below distinguish implemented foundations from requirements that remain before accepting public workloads or payments.

## Implemented Foundations

- Self-service node registration with per-node Ed25519 identities stored in macOS Keychain.
- Admin rejection of nodes and admin-only assignment of infrastructure-role labels. Provisioning, ID storage, load balancing, and economy services are not implemented yet; only compute nodes participate in the built-in diagnostic.
- PostgreSQL support, Alembic migrations, and Redis-backed expiring challenge/presence state in production mode.
- Database/Redis readiness checks, report-size bounds, and API request limits.
- A deterministic CPU/RAM credit quote and settlement calculator. It is not connected to a ledger.
- An admin-only, bounded sharded sum-of-squares diagnostic with exact result verification. It requires distinct online compute node IDs; those IDs are not proof of distinct physical machines, and physical multi-machine performance has not yet been tested.
- An admin fleet summary with SQL-aggregated self-reported CPU, RAM, storage, and GPU counts; agents refresh reports on identity recovery. These are not benchmarked or guaranteed-available capacity, and storage-free space can become stale until the next report.
- A non-root API container definition, PostgreSQL/Redis CI workflow, and a per-user macOS LaunchAgent installer.

## Blockers Before Public Staging

- Select a hosting provider and stable HTTPS domain; provision managed PostgreSQL and Redis, configure secrets, backups, restore procedures, monitoring, and alerting.
- Run the CI workflow against its PostgreSQL and Redis services. The local Compose stack has passed a smoke test, but sustained and multi-replica load tests have not been run.
- Configure edge rate limiting and verify that proxy headers cannot be spoofed. The in-app IP limiter is only a beta safeguard.
- Load-test registration and heartbeat traffic. Heartbeats use Redis for challenge/presence state but still read the durable node row from PostgreSQL; that query path needs a measured cache or partitioning strategy before very large node counts.
- Replace the Codespace URL in all test instructions. Do not leave the development port public.

## Blockers Before Running User Workloads

- Define a versioned task manifest, scheduling contract, accounting receipts, idempotency, retries, and result verification.
- Design and security-review workload isolation before executing submitted containers. The current service does not run containers or arbitrary user code.
- Define node-owner resource limits, network egress policy, storage, and abuse handling.
- Select and publish the NodeScout benchmark suite and reference score. Until then, the CPU normalization input to the credit calculator is illustrative, not an issued rate.
- Add periodic hardware report updates and validate reported CPU/RAM/storage/GPU data before using it for scheduling or pricing.
- Build an append-only credit ledger and economy-node receipt/quorum protocol. The current `economy` role and calculator do not create or settle balances.
- Add measured GPU, storage, bandwidth, and long-running service pricing before claiming those resources are covered by credits.

## Blockers Before a Public Mac Download

- Build a signed and notarized macOS package; the current LaunchAgent command is for development and requires Python plus a source checkout.
- Test install, update, restart, and removal on clean Apple Silicon Macs and supported macOS versions.
- Establish a stable production API URL and a safe update/revocation mechanism for agents.

## Economic Model Boundary

Version 1 defines one credit as one reference CPU-minute or one GiB-minute of reserved RAM, with concurrent CPU/RAM usage charged by the larger normalized quantity. Credits have no cash value, transfer, token, or ledger implementation. Keep the model versioned and do not issue balances until benchmark calibration and verifiable task receipts exist.
