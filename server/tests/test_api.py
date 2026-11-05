import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "test.sqlite3"))
    return TestClient(app)


def register(client):
    response = client.post(
        "/api/v1/nodes",
        json={
            "name": "test-mac",
            "hardware": {"cpu_cores": 8, "memory_gb": 16, "accelerators": ["Metal"]},
        },
    )
    assert response.status_code == 201
    return response.json()


def test_health_check(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_registration_returns_one_time_token_and_persists_node(client):
    registered = register(client)
    assert registered["role"] == "compute"
    assert registered["access_token"]

    response = client.get(
        f"/api/v1/nodes/{registered['node_id']}",
        headers={"Authorization": f"Bearer {registered['access_token']}"},
    )
    assert response.status_code == 200
    node = response.json()
    assert node["name"] == "test-mac"
    assert node["hardware"]["cpu_cores"] == 8
    assert "access_token" not in node


def test_registration_rejects_invalid_hardware(client):
    response = client.post(
        "/api/v1/nodes",
        json={"name": "bad-node", "hardware": {"cpu_cores": 0}},
    )
    assert response.status_code == 422


def test_node_endpoints_require_valid_credentials(client):
    registered = register(client)
    node_id = registered["node_id"]

    assert client.get(f"/api/v1/nodes/{node_id}").status_code == 401
    response = client.post(
        f"/api/v1/nodes/{node_id}/heartbeat",
        headers={"Authorization": "Bearer incorrect-token"},
    )
    assert response.status_code == 401


def test_authenticated_heartbeat_updates_last_seen(client):
    registered = register(client)
    headers = {"Authorization": f"Bearer {registered['access_token']}"}

    before = client.get(f"/api/v1/nodes/{registered['node_id']}", headers=headers).json()["last_seen"]
    heartbeat = client.post(f"/api/v1/nodes/{registered['node_id']}/heartbeat", headers=headers)
    after = client.get(f"/api/v1/nodes/{registered['node_id']}", headers=headers).json()["last_seen"]

    assert heartbeat.status_code == 200
    assert heartbeat.json()["status"] == "online"
    assert after >= before
