from __future__ import annotations

from fastapi.testclient import TestClient

from hwsim.agentic.agents import MockAgentProvider
from hwsim.agentic.simulator import AgenticGameEngine
from hwsim.web.app import GameStore, create_app


def _client() -> TestClient:
    engine = AgenticGameEngine.from_default_scenario(
        agent_provider=MockAgentProvider(),
        fallback_provider=MockAgentProvider(),
    )
    return TestClient(create_app(GameStore(engine)))


def test_agentic_api_create_command_resolve_and_reset() -> None:
    client = _client()

    created = client.post("/api/games", json={"player_faction": "liu_bei"})
    assert created.status_code == 200
    game = created.json()
    game_id = game["game_id"]
    assert game["round"] == 0
    assert game["player_faction"] == "liu_bei"

    command = client.post(
        f"/api/games/{game_id}/command",
        json={
            "strategy_text": "ally Wu, farm Yizhou, move weapons to Hanzhong",
            "policy": "logistics",
            "orders": [
                {
                    "unit_id": "liu_bei_caravan_1",
                    "action": "transfer",
                    "target_region_id": "hanzhong",
                    "resource": "weapons",
                    "amount": 16,
                }
            ],
            "diplomacy": [{"type": "propose_alliance", "target": "sun_quan", "duration_rounds": 5}],
        },
    )
    assert command.status_code == 200
    assert command.json()["current_player_policy"] == "logistics"

    resolved = client.post(f"/api/games/{game_id}/resolve")
    assert resolved.status_code == 200
    resolved_game = resolved.json()
    assert resolved_game["round"] == 1
    assert resolved_game["regional_supply"]["hanzhong"]["weapons"] >= 16
    assert resolved_game["alliances"]

    fetched = client.get(f"/api/games/{game_id}")
    assert fetched.status_code == 200
    assert fetched.json()["round"] == 1

    reset = client.post(f"/api/games/{game_id}/reset")
    assert reset.status_code == 200
    assert reset.json()["game_id"] == game_id
    assert reset.json()["round"] == 0


def test_agentic_api_404_for_unknown_game() -> None:
    client = _client()

    response = client.get("/api/games/not-real")

    assert response.status_code == 404


def test_agentic_static_ui_is_served() -> None:
    client = _client()

    response = client.get("/")

    assert response.status_code == 200
    assert "mapCanvas" in response.text
    assert "Resolve Round" in response.text
