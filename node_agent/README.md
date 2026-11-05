# Node Agent

The initial Node Agent prototype registers or recovers a node identity, then
sends signed heartbeats. On macOS, the private Ed25519 key is stored in
Keychain. It reports basic platform details for now; NodeScout integration and
workload execution are not included yet.

## Install on macOS

Use the provisioning API's HTTPS URL. In a Mac terminal:

```sh
git clone <repository-url>
cd <repository-directory>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r node_agent/requirements.txt
python -m node_agent --server https://<provisioning-server-url> --once
```

The first run creates a Keychain identity and registers the Mac. Later runs
recover its existing node ID and role. Remove the Keychain item
`DistributedCompute.NodeAgent` / `node-identity-v1` only if you intend to
create a new network identity.

To keep sending heartbeats every minute, omit `--once`:

```sh
python -m node_agent --server https://<provisioning-server-url>
```

## Install as a Background Service

After installing the requirements in the virtual environment, install a
per-user LaunchAgent:

```sh
python -m node_agent --server https://<provisioning-server-url> --install-service
python -m node_agent --service-status
```

The service starts at login, restarts if the process exits, and writes logs to
`~/Library/Logs/DistributedCompute`. It copies the agent and a dedicated
virtual environment into `~/Library/Application Support/DistributedCompute/NodeAgent`,
so it does not depend on the downloaded source folder staying in place.
During each heartbeat cycle, the agent can claim and execute one assigned
admin `sum_squares` diagnostic shard; it does not execute user-supplied code.

Remove the service with:

```sh
python -m node_agent --uninstall-service
```

Uninstall removes the LaunchAgent and its copied environment but preserves the
Keychain identity and logs. This is a development installer, not a signed or
notarized public macOS package.

## Development Test

From the repository root, install the agent requirements and run:

```sh
python -m pip install -r node_agent/requirements.txt
python -m unittest node_agent.test_client node_agent.test_macos_service
```