from pathlib import Path

from correlis_api import app as app_module
from correlis_api.scenarios import ScenarioRepository
from fastapi.testclient import TestClient

SCENARIOS = Path(__file__).parents[1] / "scenarios"
app_module.repo = ScenarioRepository(SCENARIOS)
client = TestClient(app_module.app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_scene_endpoint():
    response = client.get("/api/v1/scenarios/initial-access-demo/scene")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "confirmed"
    assert len(body["observations"]) == 7


def test_websocket_replay():
    with client.websocket_connect("/ws/scenarios/initial-access-demo/replay?speed=0") as websocket:
        started = websocket.receive_json()
        assert started["type"] == "replay_started"
        for _ in range(7):
            message = websocket.receive_json()
            assert message["type"] == "scene_delta"
        complete = websocket.receive_json()
        assert complete["type"] == "replay_complete"
        assert complete["data"]["state"] == "confirmed"
