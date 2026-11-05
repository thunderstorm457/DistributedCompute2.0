# Provisioning Server

This is the Network Foundation 1.0 starting point: nodes register a permanent
identity using an Ed25519 public key, receive a compute role by default, and
authenticate heartbeats by signing short-lived, single-use challenges. Private
keys stay with the node. Infrastructure roles are assigned only through the
admin endpoint.

## Run

From the repository root:

```sh
python -m pip install -r server/requirements.txt
PROVISIONING_ADMIN_TOKEN='replace-with-a-long-random-secret' \
  uvicorn server.main:app --host 127.0.0.1 --port 8000
```

The default SQLite database is `distributedcompute.db` in the working
directory. Set `DATABASE_URL` to use another SQLAlchemy-supported database.
Keep the admin token out of source control and expose the service only behind
TLS in a trusted deployment.

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
signature, public key, and NodeScout hardware report to `POST /v1/nodes/register`.
To recover an existing identity after restart, sign a fresh challenge with
action `recover` and an empty node ID, then submit it with the public key to
`POST /v1/nodes/recover`; the response contains the permanent node ID and role.
For heartbeats, request another challenge for the same key, use action
`heartbeat` and the assigned node ID, then submit the challenge ID and
signature to `POST /v1/nodes/{node_id}/heartbeat`.

`GET /v1/admin/nodes` returns registered node hardware and online/offline
health (a node is offline after five minutes without a heartbeat). Both admin
endpoints require the configured bearer token. The listing supports `limit`
(up to 500) and `offset` query parameters.

`PUT /v1/admin/nodes/{node_id}/role` accepts a role (`compute`, `provisioning`,
`id_storage`, or `load_balancer`) with an `Authorization: Bearer ...` header.
The endpoint is disabled until `PROVISIONING_ADMIN_TOKEN` is configured.

Run the API tests from the repository root with:

```sh
python -m unittest server.test_main
```