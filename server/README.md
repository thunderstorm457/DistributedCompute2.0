# Provisioning Server

This is the Network Foundation 1.0 starting point: nodes register a permanent
identity using an Ed25519 public key, receive a compute role by default, and
authenticate heartbeats by signing short-lived, single-use challenges. Private
keys stay with the node. Infrastructure roles are assigned only through the
admin endpoint.

## Run

From the repository root, install the server requirements and apply migrations:

```sh
python -m pip install -r server/requirements.txt
alembic upgrade head
PROVISIONING_ADMIN_TOKEN='replace-with-a-long-random-secret' \
  uvicorn server.main:app --host 127.0.0.1 --port 8000
```

The default SQLite database is `distributedcompute.db` in the working
directory. This local database is ignored by Git and is not removed by
migrations. For PostgreSQL, set `DATABASE_URL` before migrating and starting
the server:

```sh
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE'
alembic upgrade head
PROVISIONING_ADMIN_TOKEN='replace-with-a-long-random-secret' \
  uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Migrations preserve existing records; the enrollment migration marks legacy
nodes approved. Keep database credentials, Redis credentials, and the admin
token out of source control; expose the service only behind TLS in a trusted
deployment.

`GET /health` is a liveness check. `GET /ready` verifies the database and
configured Redis service. Local development defaults to process-local state.
Production must set `APP_ENV=production` and a shared `RATE_LIMIT_STORAGE_URI`,
such as a managed `rediss://` endpoint; the application refuses to start in
production with process-local memory storage. Challenges and heartbeat
presence use expiring Redis keys; PostgreSQL stores durable node identities and
roles. Challenges are limited to 30/minute and registrations to 10/hour per
client address. Hardware reports are limited to 8 KiB. These application
limits are a beta safeguard; production should also enforce rate limits at the
edge and load-test proxy address handling.

## Node Authentication

Public keys and signatures are standard Base64 encodings of the 32-byte raw
Ed25519 public key and 64-byte signature. First request a challenge:

```http
POST /v1/challenges
Content-Type: application/json

{"public_key":"<base64-ed25519-public-key>"}
```

The node signs the UTF-8 message below with its private key. The server-issued
challenge ID and nonce are used as returned; each challenge expires after five
minutes and can be consumed once.

```text
distributedcompute:v1:<action>:<node_id-or-empty>:<challenge_id>:<nonce>
```

For registration, use action `register`, an empty node ID, and submit the
signature, public key, and hardware report to `POST /v1/nodes/register`.
To recover an existing identity after restart, sign a fresh challenge with
action `recover` and an empty node ID, then submit it with the public key to
`POST /v1/nodes/recover`; the response contains the permanent node ID and role.
For heartbeats, request another challenge for the same key, use action
`heartbeat` and the assigned node ID, then submit the challenge ID and
signature to `POST /v1/nodes/{node_id}/heartbeat`.

`GET /v1/admin/nodes` returns enrollment status, hardware, and online/offline
health (a node is offline after five minutes without a heartbeat). Admin
endpoints require the configured bearer token. The listing supports `limit`
(up to 500) and `offset` query parameters.

New volunteer nodes self-register as approved compute nodes. The admin can
reject a node using `PUT /v1/admin/nodes/{node_id}/enrollment` with
`{"status":"rejected"}` and the admin bearer token. Rejected nodes cannot
heartbeat. Infrastructure roles, including `economy`, remain admin-assigned.
Existing nodes are marked approved by the enrollment migration.

`PUT /v1/admin/nodes/{node_id}/role` accepts a role (`compute`, `provisioning`,
`id_storage`, `load_balancer`, or `economy`) with an
`Authorization: Bearer ...` header. The endpoint is disabled until
`PROVISIONING_ADMIN_TOKEN` is configured. The `economy` role is reserved for
future deterministic receipt calculation; the current API has no settlement
ledger or economy-node quorum.

## Admin Split Test

The built-in `sum_squares` workload is a bounded diagnostic, not a general
container runner. It partitions a deterministic integer range into disjoint
shards and verifies each returned sum using a closed-form result. Create a run
with the admin token:

```http
POST /v1/admin/test-runs
Authorization: Bearer <admin-token>
Content-Type: application/json

{"total_items":1000000,"shard_count":2}
```

The request requires 2-64 distinct online, approved compute nodes and no more
than 2,000,000 total work items. Each node agent claims and runs only its
assigned shard after its next heartbeat. Fetch
`GET /v1/admin/test-runs/{run_id}` to see shard ranges, node IDs, durations,
throughput, and `split_verified`. That flag is true only when every range is
contiguous, every result is correct, and every shard completed on a distinct
registered node ID. This is not hardware attestation: two IDs do not prove two
physical computers. Runs fail after 15 minutes. The workload has not yet been
measured across separate physical machines; do that only after connecting at
least two nodes.

## Container Deployment

The root `Dockerfile` builds a non-root API image. Set `DATABASE_URL`,
`RATE_LIMIT_STORAGE_URI`, `APP_ENV=production`, and
`PROVISIONING_ADMIN_TOKEN` through the hosting provider's secret manager. Run
`alembic upgrade head` once as a deployment migration step before starting or
scaling API replicas; do not have every replica race to migrate on startup.
Configure the provider's readiness probe to use `/ready`.

For a local PostgreSQL/Redis staging stack, copy `.env.example` to `.env`,
change both values, then run:

```sh
docker compose up --build
```

The Compose migration service runs once before the API starts. The local
PostgreSQL volume persists across restarts; remove it only when intentionally
discarding local test data. This Compose file is for development/staging, not
a substitute for managed production services or backups.

The CI workflow runs PostgreSQL migrations and API tests with shared Redis.
The local Compose stack was smoke-tested through migration, registration,
heartbeat, and readiness. These prepare the service for staging, but do not
deploy it or provide hosting, backups, monitoring, or a public domain.

Run the API, migration, economics, and agent tests from the repository root:

```sh
python -m unittest server.test_main server.test_migrations server.test_economics node_agent.test_client node_agent.test_macos_service
```