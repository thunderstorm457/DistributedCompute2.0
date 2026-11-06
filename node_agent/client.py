import argparse
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol, Union

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


KEYCHAIN_SERVICE = "DistributedCompute.NodeAgent"
KEYCHAIN_ACCOUNT = "node-identity-v1"
GPU_CORE_KEYS = ("sppci_cores", "spdisplays_cores", "gpu_core_count", "gpu_cores")


def _extract_gpu_core_counts(value):
    if isinstance(value, dict):
        for key in GPU_CORE_KEYS:
            if key in value:
                match = re.search(r"\d+", str(value[key]))
                if match:
                    return [int(match.group())]
        return [
            count
            for child in value.values()
            for count in _extract_gpu_core_counts(child)
        ]
    if isinstance(value, list):
        return [count for child in value for count in _extract_gpu_core_counts(child)]
    return []


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
    status: str


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

    def _challenge(
        self, action: str, node_id: str = "", context: str = ""
    ) -> tuple[str, str]:
        response = self.client.post(
            "/v1/challenges", json={"public_key": self.public_key}
        )
        response.raise_for_status()
        challenge = response.json()
        message = (
            f"distributedcompute:v1:{action}:{node_id}:"
            f"{challenge['challenge_id']}:{challenge['challenge']}"
        )
        if context:
            message = f"{message}:{context}"
        message = message.encode("utf-8")
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
        return NodeInfo(node_id=body["node_id"], role=body["role"], status=body["status"])

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
        return NodeInfo(node_id=body["node_id"], role=body["role"], status=body["status"])

    def connect(self) -> NodeInfo:
        recovered = self._recover()
        if recovered is None:
            self.node = self._register()
        else:
            self.node = recovered
            self.update_hardware()
        return self.node

    def update_hardware(self) -> None:
        if self.node is None:
            raise RuntimeError("Connect to the provisioning server before updating hardware")
        hardware = self.hardware_report()
        serialized = json.dumps(
            hardware, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        context = hashlib.sha256(serialized).hexdigest()
        challenge_id, signature = self._challenge(
            "update_hardware", self.node.node_id, context
        )
        response = self.client.put(
            f"/v1/nodes/{self.node.node_id}/hardware",
            json={
                "challenge_id": challenge_id,
                "hardware": hardware,
                "signature": signature,
            },
        )
        response.raise_for_status()

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
        self.node = NodeInfo(
            node_id=body["node_id"], role=body["role"], status=body["status"]
        )
        return self.node

    def process_test_task(self) -> Optional[Dict[str, Union[str, int, bool]]]:
        if self.node is None:
            raise RuntimeError("Connect to the provisioning server before claiming tasks")
        challenge_id, signature = self._challenge(
            "claim_test_task", self.node.node_id
        )
        response = self.client.post(
            f"/v1/nodes/{self.node.node_id}/test-tasks/claim",
            json={"challenge_id": challenge_id, "signature": signature},
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        task = response.json()
        item_count = task["end_exclusive"] - task["start_inclusive"]
        sum_squares = sum(
            item * item
            for item in range(task["start_inclusive"], task["end_exclusive"])
        )
        context = f"{task['task_id']}:{item_count}:{sum_squares}"
        challenge_id, signature = self._challenge(
            "complete_test_task", self.node.node_id, context
        )
        completion = self.client.post(
            f"/v1/nodes/{self.node.node_id}/test-tasks/{task['task_id']}/complete",
            json={
                "challenge_id": challenge_id,
                "item_count": item_count,
                "sum_squares": sum_squares,
                "signature": signature,
            },
        )
        completion.raise_for_status()
        return completion.json()

    @staticmethod
    def hardware_report() -> Dict[str, Union[str, int, None]]:
        ram_bytes = None
        gpu_cores = None
        if sys.platform == "darwin":
            try:
                memory = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                ram_bytes = int(memory.stdout.strip())
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            try:
                displays = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType", "-json"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                core_counts = _extract_gpu_core_counts(json.loads(displays.stdout))
                gpu_cores = sum(core_counts) if core_counts else None
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

        try:
            storage = shutil.disk_usage(Path.home())
            storage_total_bytes = storage.total
            storage_available_bytes = storage.free
        except OSError:
            storage_total_bytes = None
            storage_available_bytes = None

        return {
            "agent_version": "0.1.0",
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "logical_cpu_cores": os.cpu_count(),
            "ram_bytes": ram_bytes,
            "storage_total_bytes": storage_total_bytes,
            "storage_available_bytes": storage_available_bytes,
            "gpu_cores": gpu_cores,
        }

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Join and monitor a DistributedCompute node")
    parser.add_argument("--server", help="Provisioning API base URL")
    parser.add_argument("--interval", type=float, default=60, help="Heartbeat interval in seconds")
    parser.add_argument(
        "--once", action="store_true", help="Register/recover, send one heartbeat, then exit"
    )
    service_actions = parser.add_mutually_exclusive_group()
    service_actions.add_argument("--install-service", action="store_true")
    service_actions.add_argument("--uninstall-service", action="store_true")
    service_actions.add_argument("--service-status", action="store_true")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    if args.once and (args.install_service or args.uninstall_service or args.service_status):
        parser.error("--once cannot be combined with a service command")

    try:
        if args.install_service:
            if not args.server:
                parser.error("--install-service requires --server")
            from .macos_service import install_service

            install_service(args.server, args.interval)
            print("Node Agent installed and started as a per-user LaunchAgent")
            return
        if args.uninstall_service:
            from .macos_service import uninstall_service

            uninstall_service()
            print("Node Agent service removed; Keychain identity was preserved")
            return
        if args.service_status:
            from .macos_service import service_status

            print(service_status())
            return
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Node Agent service operation failed: {exc}\n")

    if not args.server:
        parser.error("--server is required unless using a service command")

    try:
        agent = NodeAgent(args.server, KeychainIdentityStore())
    except Exception as exc:
        parser.exit(1, f"Unable to load the node identity: {exc}\n")

    try:
        node = agent.connect()
        print(
            f"Node {node.node_id} is {node.status} with role {node.role}", flush=True
        )
        while True:
            try:
                agent.heartbeat()
                print(f"Heartbeat sent for node {node.node_id}", flush=True)
                task_result = agent.process_test_task()
                if task_result:
                    print(
                        f"Verified test shard {task_result['task_id']} completed",
                        flush=True,
                    )
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