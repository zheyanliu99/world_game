from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hwsim.agentic.agents import MockAgentProvider
from hwsim.agentic.simulator import AgenticGameEngine
from hwsim.web.app import GameStore, create_app


ROOT = Path(__file__).resolve().parents[1]


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
    assert game["max_rounds"] == 100
    assert game["real_map"]["canvas_size"] == [1280, 720]
    assert game["real_map"]["grid_size"] == [480, 270]
    assert len(game["real_map"]["provinces"]) >= 30
    assert game["real_map"]["region_polygons"] == []
    assert game["real_map"]["rivers"] == []
    assert game["real_map"]["terrain_lines"] == []
    assert len(game["cities"]) >= 45
    map_width, map_height = game["real_map"]["canvas_size"]
    assert all(0 <= city["position"][0] <= map_width and 0 <= city["position"][1] <= map_height for city in game["cities"])
    assert len(game["roads"]) >= 80
    assert {"from_city_id", "to_city_id", "food_cost", "gold_cost", "soldier_loss_bps"} <= set(game["roads"][0])
    assert game["city_stacks"]
    assert {"leader_general_id", "general_count", "total_soldiers"} <= set(game["city_stacks"][0])
    assert any(general["portrait_path"].endswith(".svg") for general in game["generals"])
    assert game["advisor_recommendation"]["summary"]
    assert game["advisor_recommendation"]["orders"]

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
    assert "alliances" in resolved_game
    assert "active_battles" in resolved_game
    assert "incident_events" in resolved_game
    assert "general_discovery_events" in resolved_game

    fetched = client.get(f"/api/games/{game_id}")
    assert fetched.status_code == 200
    assert fetched.json()["round"] == 1
    assert fetched.json()["advisor_recommendation"]["policy"]

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
    app_js = (ROOT / "src/hwsim/web/static/app.js").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "mapCanvas" in response.text
    assert "vendor/phaser.min.js" in response.text
    assert "结算回合" in response.text
    assert "battleList" in response.text
    assert "advisorList" in response.text
    assert "drawPhaserRoads();" not in app_js
