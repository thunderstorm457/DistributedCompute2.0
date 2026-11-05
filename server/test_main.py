import base64
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from node_agent.client import NodeAgent
from server import main


class ProvisioningApiTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(
            f"sqlite:///{self.database_path}", connect_args={"check_same_thread": False}
        )
        main.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            with self.sessions() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = base64.b64encode(
            self.private_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode("ascii")

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.unlink(self.database_path)

    def issue_challenge(self):
        response = self.client.post("/v1/challenges", json={"public_key": self.public_key})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def sign(self, action, challenge, node_id=""):
        message = main.signed_message(
            action, challenge["challenge_id"], challenge["challenge"], node_id
        )
        return base64.b64encode(self.private_key.sign(message)).decode("ascii")

    def test_registration_and_signed_heartbeat(self):
        challenge = self.issue_challenge()
        registration = self.client.post(
            "/v1/nodes/register",
            json={
                "challenge_id": challenge["challenge_id"],
                "public_key": self.public_key,
                "signature": self.sign("register", challenge),
                "hardware": {"cpu_cores": 8, "memory_bytes": 17179869184},
            },
        )
        self.assertEqual(registration.status_code, 201)
        node = registration.json()
        self.assertEqual(node["role"], "compute")

        challenge = self.issue_challenge()
        heartbeat = self.client.post(
            f"/v1/nodes/{node['node_id']}/heartbeat",
            json={
                "challenge_id": challenge["challenge_id"],
                "signature": self.sign("heartbeat", challenge, node["node_id"]),
            },
        )
        self.assertEqual(heartbeat.status_code, 200)
        self.assertGreaterEqual(
            datetime.fromisoformat(heartbeat.json()["last_seen"]),
            datetime.fromisoformat(node["last_seen"]),
        )

        replay = self.client.post(
            f"/v1/nodes/{node['node_id']}/heartbeat",
            json={
                "challenge_id": challenge["challenge_id"],
                "signature": self.sign("heartbeat", challenge, node["node_id"]),
            },
        )
        self.assertEqual(replay.status_code, 401)

    def test_infrastructure_role_requires_admin_token(self):
        challenge = self.issue_challenge()
        registration = self.client.post(
            "/v1/nodes/register",
            json={
                "challenge_id": challenge["challenge_id"],
                "public_key": self.public_key,
                "signature": self.sign("register", challenge),
                "hardware": {},
            },
        )
        node_id = registration.json()["node_id"]

        with patch.dict(os.environ, {"PROVISIONING_ADMIN_TOKEN": "test-secret"}):
            denied = self.client.put(
                f"/v1/admin/nodes/{node_id}/role", json={"role": "id_storage"}
            )
            self.assertEqual(denied.status_code, 403)
            assigned = self.client.put(
                f"/v1/admin/nodes/{node_id}/role",
                json={"role": "id_storage"},
                headers={"Authorization": "Bearer test-secret"},
            )
            nodes = self.client.get(
                "/v1/admin/nodes", headers={"Authorization": "Bearer test-secret"}
            )
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.json()["role"], "id_storage")
        self.assertEqual(nodes.status_code, 200)
        self.assertEqual(nodes.json()[0]["status"], "online")

    def test_registered_identity_can_recover_its_node_id(self):
        challenge = self.issue_challenge()
        registration = self.client.post(
            "/v1/nodes/register",
            json={
                "challenge_id": challenge["challenge_id"],
                "public_key": self.public_key,
                "signature": self.sign("register", challenge),
                "hardware": {},
            },
        )
        original_node_id = registration.json()["node_id"]

        challenge = self.issue_challenge()
        recovered = self.client.post(
            "/v1/nodes/recover",
            json={
                "challenge_id": challenge["challenge_id"],
                "public_key": self.public_key,
                "signature": self.sign("recover", challenge),
            },
        )
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["node_id"], original_node_id)

    def test_node_agent_completes_registration_recovery_and_heartbeat(self):
        identity_store = MemoryIdentityStore()
        agent = NodeAgent("http://testserver", identity_store, client=self.client)

        registered = agent.connect()
        self.assertEqual(registered.role, "compute")
        heartbeat = agent.heartbeat()
        self.assertEqual(heartbeat.node_id, registered.node_id)

        recovered = agent.connect()
        self.assertEqual(recovered.node_id, registered.node_id)


class MemoryIdentityStore:
    def __init__(self):
        self.private_key = Ed25519PrivateKey.generate()

    def load_or_create_private_key(self):
        return self.private_key


if __name__ == "__main__":
    unittest.main()