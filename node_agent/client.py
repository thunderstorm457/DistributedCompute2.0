import argparse
import base64
import os
import platform
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Union

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


KEYCHAIN_SERVICE = "DistributedCompute.NodeAgent"
KEYCHAIN_ACCOUNT = "node-identity-v1"


class IdentityStore(Protocol):
    def load_or_create_private_key(self) -> Ed25519PrivateKey: ...


class KeychainIdentityStore:
    def load_or_create_private_key(self) -> Ed25519PrivateKey:
        if sys.platform != "darwin":
            raise RuntimeError("The default identity store requires macOS Keychain")

        import keyring

        backend = keyring.get_keyring()
        if backend.priority < 1:
            raise RuntimeError("No secure macOS Keychain backend is available")

        stored_key = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        if stored_key:
            try:
                raw_key = base64.b64decode(stored_key, validate=True)
                return Ed25519PrivateKey.from_private_bytes(raw_key)
            except (ValueError, TypeError) as exc:
                raise RuntimeError("The Keychain identity is invalid; refusing to replace it") from exc

        private_key = Ed25519PrivateKey.generate()
        raw_key = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        keyring.set_password(
            KEYCHAIN_SERVICE,
            KEYCHAIN_ACCOUNT,
            base64.b64encode(raw_key).decode("ascii"),
        )
        return private_key


@dataclass(frozen=True)
class NodeInfo:
    node_id: str
    role: str


class NodeAgent:
    def __init__(
        self,
        server_url: str,
        identity_store: IdentityStore,
        transport: Optional[httpx.BaseTransport] = None,
        client: Optional[httpx.Client] = None,
    ):
        self.private_key = identity_store.load_or_create_private_key()
        self.public_key = base64.b64encode(
            self.private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii")
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=server_url.rstrip("/"), timeout=15, transport=transport
        )
        self.node: Optional[NodeInfo] = None

    def _challenge(self, action: str, node_id: str = "") -> tuple[str, str]:
        response = self.client.post(
            "/v1/challenges", json={"public_key": self.public_key}
        )
        response.raise_for_status()
        challenge = response.json()
        message = (
            f"distributedcompute:v1:{action}:{node_id}:"
            f"{challenge['challenge_id']}:{challenge['challenge']}"
        ).encode("utf-8")
        signature = base64.b64encode(self.private_key.sign(message)).decode("ascii")
        return challenge["challenge_id"], signature

    def _recover(self) -> Optional[NodeInfo]:
        challenge_id, signature = self._challenge("recover")
        response = self.client.post(
            "/v1/nodes/recover",
            json={
                "challenge_id": challenge_id,
                "public_key": self.public_key,
                "signature": signature,
            },
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        return NodeInfo(node_id=body["node_id"], role=body["role"])

    def _register(self) -> NodeInfo:
        challenge_id, signature = self._challenge("register")
        response = self.client.post(
            "/v1/nodes/register",
            json={
                "challenge_id": challenge_id,
                "public_key": self.public_key,
                "signature": signature,
                "hardware": self.hardware_report(),
            },
        )
        if response.status_code == 409:
            recovered = self._recover()
            if recovered:
                return recovered
        response.raise_for_status()
        body = response.json()
        return NodeInfo(node_id=body["node_id"], role=body["role"])

    def connect(self) -> NodeInfo:
        self.node = self._recover() or self._register()
        return self.node

    def heartbeat(self) -> NodeInfo:
        if self.node is None:
            raise RuntimeError("Connect to the provisioning server before heartbeating")
        challenge_id, signature = self._challenge("heartbeat", self.node.node_id)
        response = self.client.post(
            f"/v1/nodes/{self.node.node_id}/heartbeat",
            json={"challenge_id": challenge_id, "signature": signature},
        )
        response.raise_for_status()
        body = response.json()
        self.node = NodeInfo(node_id=body["node_id"], role=body["role"])
        return self.node

    @staticmethod
    def hardware_report() -> Dict[str, Union[str, int, None]]:
        return {
            "agent_version": "0.1.0",
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
        }

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Join and monitor a DistributedCompute node")
    parser.add_argument("--server", required=True, help="Provisioning API base URL")
    parser.add_argument("--interval", type=float, default=60, help="Heartbeat interval in seconds")
    parser.add_argument(
        "--once", action="store_true", help="Register/recover, send one heartbeat, then exit"
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")

    try:
        agent = NodeAgent(args.server, KeychainIdentityStore())
    except Exception as exc:
        parser.exit(1, f"Unable to load the node identity: {exc}\n")

    try:
        node = agent.connect()
        print(f"Node {node.node_id} connected with role {node.role}", flush=True)
        while True:
            try:
                agent.heartbeat()
                print(f"Heartbeat sent for node {node.node_id}", flush=True)
            except httpx.HTTPError as exc:
                if args.once:
                    raise
                print(f"Heartbeat failed: {exc}", file=sys.stderr, flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Node Agent stopped", flush=True)
    except httpx.HTTPError as exc:
        parser.exit(1, f"Provisioning request failed: {exc}\n")
    finally:
        agent.close()


if __name__ == "__main__":
    main()