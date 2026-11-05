import base64
import os
import tempfile
import unittest
import uuid
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
        self.client = TestClient(main.app, client=(f"test-{uuid.uuid4()}", 12345))
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

    def sign(self, action, challenge, node_id="", context=""):
        message = main.signed_message(
            action,
            challenge["challenge_id"],
            challenge["challenge"],
            node_id,
            context,
        )
        return base64.b64encode(self.private_key.sign(message)).decode("ascii")

    def issue_challenge_for_agent(self, agent):
        response = self.client.post(
            "/v1/challenges", json={"public_key": agent.public_key}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def sign_agent(self, agent, action, challenge, node_id="", context=""):
        message = main.signed_message(
            action,
            challenge["challenge_id"],
            challenge["challenge"],
            node_id,
            context,
        )
        return base64.b64encode(agent.private_key.sign(message)).decode("ascii")

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
        self.assertEqual(node["status"], "approved")

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
        with self.sessions() as db:
            persisted_node = db.get(main.Node, node["node_id"])
        self.assertEqual(
            persisted_node.last_seen.replace(tzinfo=datetime.fromisoformat(node["last_seen"]).tzinfo),
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

    def test_database_readiness_and_hardware_report_size_limit(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/ready").json(), {"status": "ready"})

        challenge = self.issue_challenge()
        registration = self.client.post(
            "/v1/nodes/register",
            json={
                "challenge_id": challenge["challenge_id"],
                "public_key": self.public_key,
                "signature": self.sign("register", challenge),
                "hardware": {"details": "x" * (main.MAX_HARDWARE_REPORT_BYTES + 1)},
            },
        )
        self.assertEqual(registration.status_code, 422)

    def test_challenge_endpoint_rate_limit(self):
        last_response = None
        for _ in range(31):
            last_response = self.client.post(
                "/v1/challenges", json={"public_key": self.public_key}
            )
        self.assertEqual(last_response.status_code, 429)

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
            approved = self.client.put(
                f"/v1/admin/nodes/{node_id}/enrollment",
                json={"status": "approved"},
                headers={"Authorization": "Bearer test-secret"},
            )
            denied = self.client.put(
                f"/v1/admin/nodes/{node_id}/role", json={"role": "id_storage"}
            )
            self.assertEqual(denied.status_code, 403)
            assigned = self.client.put(
                f"/v1/admin/nodes/{node_id}/role",
                json={"role": "id_storage"},
                headers={"Authorization": "Bearer test-secret"},
            )
            economy_role = self.client.put(
                f"/v1/admin/nodes/{node_id}/role",
                json={"role": "economy"},
                headers={"Authorization": "Bearer test-secret"},
            )
            nodes = self.client.get(
                "/v1/admin/nodes", headers={"Authorization": "Bearer test-secret"}
            )
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")
        self.assertEqual(assigned.json()["role"], "id_storage")
        self.assertEqual(economy_role.status_code, 200)
        self.assertEqual(economy_role.json()["role"], "economy")
        self.assertEqual(nodes.status_code, 200)
        self.assertEqual(nodes.json()[0]["health"], "online")

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
        self.assertEqual(recovered.json()["status"], "approved")

    def test_node_agent_completes_registration_recovery_and_heartbeat(self):
        identity_store = MemoryIdentityStore()
        agent = NodeAgent("http://testserver", identity_store, client=self.client)

        registered = agent.connect()
        self.assertEqual(registered.role, "compute")
        self.assertEqual(registered.status, "approved")
        heartbeat = agent.heartbeat()
        self.assertEqual(heartbeat.node_id, registered.node_id)

        recovered = agent.connect()
        self.assertEqual(recovered.node_id, registered.node_id)

    def test_rejected_nodes_cannot_heartbeat_or_receive_a_role(self):
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
            rejected = self.client.put(
                f"/v1/admin/nodes/{node_id}/enrollment",
                json={"status": "rejected"},
                headers={"Authorization": "Bearer test-secret"},
            )
            role = self.client.put(
                f"/v1/admin/nodes/{node_id}/role",
                json={"role": "provisioning"},
                headers={"Authorization": "Bearer test-secret"},
            )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(role.status_code, 409)

        challenge = self.issue_challenge()
        heartbeat = self.client.post(
            f"/v1/nodes/{node_id}/heartbeat",
            json={
                "challenge_id": challenge["challenge_id"],
                "signature": self.sign("heartbeat", challenge, node_id),
            },
        )
        self.assertEqual(heartbeat.status_code, 403)

    def register_agent(self):
        agent = NodeAgent("http://testserver", MemoryIdentityStore(), client=self.client)
        return agent, agent.connect()

    def test_admin_test_run_completes_distinct_verified_shards(self):
        first_agent, first_node = self.register_agent()
        second_agent, second_node = self.register_agent()
        self.assertNotEqual(first_node.node_id, second_node.node_id)

        with patch.dict(os.environ, {"PROVISIONING_ADMIN_TOKEN": "test-secret"}):
            created = self.client.post(
                "/v1/admin/test-runs",
                json={"total_items": 1001, "shard_count": 2},
                headers={"Authorization": "Bearer test-secret"},
            )
        self.assertEqual(created.status_code, 201)
        initial = created.json()
        self.assertEqual(initial["status"], "running")
        self.assertEqual(
            {shard["node_id"] for shard in initial["shards"]},
            {first_node.node_id, second_node.node_id},
        )
        self.assertEqual(initial["shards"][0]["start_inclusive"], 0)
        self.assertEqual(initial["shards"][0]["end_exclusive"], initial["shards"][1]["start_inclusive"])
        self.assertEqual(initial["shards"][1]["end_exclusive"], 1001)

        first_result = first_agent.process_test_task()
        second_result = second_agent.process_test_task()
        self.assertTrue(first_result["verified"])
        self.assertTrue(second_result["verified"])

        with patch.dict(os.environ, {"PROVISIONING_ADMIN_TOKEN": "test-secret"}):
            result = self.client.get(
                f"/v1/admin/test-runs/{initial['run_id']}",
                headers={"Authorization": "Bearer test-secret"},
            )
        self.assertEqual(result.status_code, 200)
        summary = result.json()
        self.assertEqual(summary["status"], "completed")
        self.assertTrue(summary["split_verified"])
        self.assertEqual(summary["participating_nodes"], 2)
        self.assertEqual(summary["completed_shards"], 2)

    def test_admin_test_requires_multiple_nodes_and_admin_auth(self):
        agent, _ = self.register_agent()
        request_body = {"total_items": 100, "shard_count": 2}
        with patch.dict(os.environ, {"PROVISIONING_ADMIN_TOKEN": "test-secret"}):
            denied = self.client.post("/v1/admin/test-runs", json=request_body)
        self.assertEqual(denied.status_code, 403)

        with patch.dict(os.environ, {"PROVISIONING_ADMIN_TOKEN": "test-secret"}):
            insufficient_nodes = self.client.post(
                "/v1/admin/test-runs",
                json=request_body,
                headers={"Authorization": "Bearer test-secret"},
            )
        self.assertEqual(insufficient_nodes.status_code, 409)
        agent.close()

    def test_admin_test_rejects_an_incorrect_signed_shard_result(self):
        first_agent, first_node = self.register_agent()
        second_agent, _ = self.register_agent()
        with patch.dict(os.environ, {"PROVISIONING_ADMIN_TOKEN": "test-secret"}):
            created = self.client.post(
                "/v1/admin/test-runs",
                json={"total_items": 100, "shard_count": 2},
                headers={"Authorization": "Bearer test-secret"},
            )
        run = created.json()

        challenge = self.issue_challenge_for_agent(first_agent)
        claim = self.client.post(
            f"/v1/nodes/{first_node.node_id}/test-tasks/claim",
            json={
                "challenge_id": challenge["challenge_id"],
                "signature": self.sign_agent(
                    first_agent,
                    "claim_test_task", challenge, first_node.node_id
                ),
            },
        )
        self.assertEqual(claim.status_code, 200)
        task = claim.json()
        item_count = task["end_exclusive"] - task["start_inclusive"]
        wrong_result = 1
        context = f"{task['task_id']}:{item_count}:{wrong_result}"
        challenge = self.issue_challenge_for_agent(first_agent)
        rejected = self.client.post(
            f"/v1/nodes/{first_node.node_id}/test-tasks/{task['task_id']}/complete",
            json={
                "challenge_id": challenge["challenge_id"],
                "item_count": item_count,
                "sum_squares": wrong_result,
                "signature": self.sign_agent(
                    first_agent,
                    "complete_test_task", challenge, first_node.node_id, context
                ),
            },
        )
        self.assertEqual(rejected.status_code, 422)
        with patch.dict(os.environ, {"PROVISIONING_ADMIN_TOKEN": "test-secret"}):
            status = self.client.get(
                f"/v1/admin/test-runs/{run['run_id']}",
                headers={"Authorization": "Bearer test-secret"},
            )
        self.assertEqual(status.json()["status"], "failed")
        first_agent.close()
        second_agent.close()


class MemoryIdentityStore:
    def __init__(self):
        self.private_key = Ed25519PrivateKey.generate()

    def load_or_create_private_key(self):
        return self.private_key


if __name__ == "__main__":
    unittest.main()