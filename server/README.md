# Provisioning API MVP

This is the first runnable Network Foundation milestone for DistributedCompute. It provides a small provisioning service for node registration, persistent node records, role assignment, and authenticated health heartbeats. It does **not** execute submitted code, manage volunteer machines remotely, or grant infrastructure roles.

## Requirements

- Python 3.10 or newer
- `pip`

## Run locally

From the `server/` directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

The SQLite database defaults to `distributedcompute.sqlite3` in the current working directory. Set `DISTRIBUTEDCOMPUTE_DB` to choose another path. The API docs are available at `http://127.0.0.1:8000/docs`; liveness is at `GET /healthz`.

## API

### Register a node

`POST /api/v1/nodes`

```json
{
  "name": "my-mac",
  "hardware": {
    "cpu_cores": 8,
    "memory_gb": 16,
    "accelerators": ["Metal"]
  }
}
```

Returns `201 Created` with a permanent UUID node ID, the assigned `compute` role, and a randomly generated bearer token. **Save the token securely:** it is returned only during registration, while only its SHA-256 digest is stored in SQLite. This prototype does not yet support token rotation or recovery if a token is lost.

### Send a heartbeat

`POST /api/v1/nodes/{node_id}/heartbeat`

Send `Authorization: Bearer <access_token>`. A successful response updates the node's `last_seen` timestamp and reports it as online.

### Read the node's own record

`GET /api/v1/nodes/{node_id}`

Uses the same bearer authentication. Nodes cannot read another node's record using their own token.

## Tests

From `server/`:

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Scope and security notes

This is a development prototype, not a public volunteer service. Registration is intentionally open for local development; before exposing it to a network, add an enrollment/administrator policy, HTTPS termination, rate limits, token rotation/revocation, structured audit logging, and operational monitoring. Do not send private keys, passwords, or other secrets in hardware reports. Infrastructure roles remain unassigned, and this API does not accept or execute workloads.
