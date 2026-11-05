import base64
import json
import unittest
import uuid
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from node_agent.client import NodeAgent


class MemoryIdentityStore:
    def __init__(self):
        self.private_key = Ed25519PrivateKey.generate()

    def load_or_create_private_key(self):
        return self.private_key


class FakeProvisioningServer:
    def __init__(self):
        self.challenges = {}
        self.nodes = {}
        self.registrations = 0
        self.heartbeats = 0

    def __call__(self, request):
        body = json.loads(request.content or b"{}")
        if request.url.path == "/v1/challenges":
            challenge_id = str(uuid.uuid4())
            nonce = base64.b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode()
            self.challenges[challenge_id] = {
                "public_key": body["public_key"],
                "nonce": nonce,
                "used": False,
            }
            return httpx.Response(
                200,
                json={
                    "challenge_id": challenge_id,
                    "challenge": nonce,
                    "expires_at": "2030-01-01T00:00:00+00:00",
                },
            )

        if request.url.path == "/v1/nodes/recover":
            challenge = self._verify(body, "recover")
            node = self.nodes.get(challenge["public_key"])
            return httpx.Response(404) if node is None else httpx.Response(200, json=node)

        if request.url.path == "/v1/nodes/register":
            self._verify(body, "register")
            self.registrations += 1
            now = datetime.now(timezone.utc).isoformat()
            node = {
                "node_id": str(uuid.uuid4()),
                "role": "compute",
                "status": "approved",
                "registered_at": now,
                "last_seen": now,
            }
            self.nodes[body["public_key"]] = node
            return httpx.Response(201, json=node)

        if request.url.path.endswith("/heartbeat"):
            node_id = request.url.path.split("/")[-2]
            challenge = self._verify(body, "heartbeat", node_id)
            node = self.nodes[challenge["public_key"]]
            self.heartbeats += 1
            node["last_seen"] = datetime.now(timezone.utc).isoformat()
            return httpx.Response(200, json=node)

        return httpx.Response(404)

    def _verify(self, body, action, node_id=""):
        challenge = self.challenges[body["challenge_id"]]
        if challenge["used"]:
            return httpx.Response(401)
        message = (
            f"distributedcompute:v1:{action}:{node_id}:"
            f"{body['challenge_id']}:{challenge['nonce']}"
        ).encode()
        public_key = base64.b64decode(challenge["public_key"])
        signature = base64.b64decode(body["signature"])
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        challenge["used"] = True
        return challenge


class NodeAgentTests(unittest.TestCase):
    def test_agent_registers_recovers_and_sends_heartbeats(self):
        server = FakeProvisioningServer()
        agent = NodeAgent(
            "https://provisioning.example",
            MemoryIdentityStore(),
            transport=httpx.MockTransport(server),
        )
        try:
            first = agent.connect()
            self.assertEqual(first.role, "compute")
            self.assertEqual(server.registrations, 1)

            heartbeat = agent.heartbeat()
            self.assertEqual(heartbeat.node_id, first.node_id)
            self.assertEqual(server.heartbeats, 1)

            recovered = agent.connect()
            self.assertEqual(recovered.node_id, first.node_id)
            self.assertEqual(server.registrations, 1)
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()