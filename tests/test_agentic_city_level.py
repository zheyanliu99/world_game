from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

from hwsim.agentic.agents import MockAgentProvider
from hwsim.agentic.city_data import load_city_graph, load_general_seeds
from hwsim.agentic.models import AgentOrder, AgenticAlliance
from hwsim.agentic.simulator import AgenticGameEngine


ROOT = Path(__file__).resolve().parents[1]


def _engine() -> AgenticGameEngine:
    return AgenticGameEngine.from_default_scenario(agent_provider=MockAgentProvider(), fallback_provider=MockAgentProvider())


def test_city_graph_and_general_seed_validate() -> None:
    graph = load_city_graph()
    seeds = load_general_seeds(city_ids=set(graph.city_map()))

    assert 25 <= len(graph.cities) <= 40
    assert any(city.name_cn == "建业" for city in graph.cities)
    assert any(seed.name_cn == "关羽" and seed.soldiers > 10000 for seed in seeds)
    assert len([seed for seed in seeds if seed.faction_id == "cao"]) == 8
    assert next(seed for seed in seeds if seed.id == "cao_cao").soldiers == 35000
    assert next(seed for seed in seeds if seed.id == "xiahou_dun").max_soldiers == 32500
    assert all((ROOT / "src/hwsim/web/static" / seed.portrait_path.removeprefix("/static/")).exists() for seed in seeds)


def test_bigquery_schema_matches_csv_seed() -> None:
    schema = json.loads((ROOT / "data/generals/general_seed_bigquery_schema.json").read_text(encoding="utf-8"))
    with (ROOT / "data/generals/general_seed.csv").open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert reader.fieldnames == [field["name"] for field in schema]
    assert len(rows) >= 18
    assert {field["type"] for field in schema} <= {"STRING", "INTEGER", "FLOAT"}


def test_bigquery_loader_validate_only() -> None:
    result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "scripts/load_general_seed_bigquery.py", "--validate-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Validated" in result.stdout


def test_city_level_initialization_derives_region_summary() -> None:
    state = _engine().new_game(game_id="city-init")

    assert state.cities
    assert state.city_owners["chengdu"] == "liu_bei"
    assert state.region_owners["yizhou"] == "liu_bei"
    assert len([general for general in state.generals.values() if general.faction_id == "cao"]) == 8
    guan_yu = state.generals["guan_yu"]
    assert guan_yu.city_id == "xiangyang"
    assert guan_yu.unit_id == "liu_bei_army_1"
    assert any(unit.general_id == "guan_yu" and unit.soldiers == guan_yu.soldiers for unit in state.units)


def test_non_adjacent_city_attack_is_rejected() -> None:
    engine = _engine()
    state = engine.new_game(game_id="illegal-city")
    engine.save_player_command(
        state,
        "try impossible march",
        "war",
        orders=[
            AgentOrder(
                unit_id="liu_bei_army_1",
                general_id="guan_yu",
                action="attack",
                source_city_id="xiangyang",
                target_city_ids=["beiping"],
            )
        ],
    )

    engine.resolve_round(state)

    assert state.city_owners["beiping"] == "cao"
    assert any("no adjacent city target" in log.detail for log in state.logs)


def test_city_alliance_blocks_attack() -> None:
    engine = _engine()
    state = engine.new_game(game_id="allied-city")
    state.alliances.append(AgenticAlliance(factions=("liu_bei", "sun_quan"), expires_round=3))
    engine.save_player_command(
        state,
        "do not actually fight Wu",
        "war",
        orders=[
            AgentOrder(
                unit_id="liu_bei_army_1",
                general_id="guan_yu",
                action="attack",
                source_city_id="xiangyang",
                target_city_ids=["changsha"],
            )
        ],
    )

    engine.resolve_round(state)

    assert state.city_owners["changsha"] == "sun_quan"
    assert any("Alliance blocks" in log.detail for log in state.logs)


def test_city_battle_records_animation_and_soldier_losses() -> None:
    engine = _engine()
    state = engine.new_game(game_id="city-battle")
    before = next(unit.soldiers for unit in state.units if unit.id == "liu_bei_army_5")
    engine.save_player_command(
        state,
        "attack Tianshui",
        "war",
        orders=[
            AgentOrder(
                unit_id="liu_bei_army_5",
                general_id="ma_chao",
                action="attack",
                source_city_id="wudu",
                target_city_ids=["tianshui"],
            )
        ],
    )

    engine.resolve_round(state)

    battle = next(event for event in state.battle_events if event.attacker_general_id == "ma_chao")
    assert battle.target_city_id == "tianshui"
    assert 0.12 <= battle.win_probability <= 0.88
    assert any(event.type in {"clash", "retreat"} for event in state.animations)
    after = next(unit.soldiers for unit in state.units if unit.id == "liu_bei_army_5")
    assert after < before


def test_city_transfer_adds_exact_city_and_region_supply() -> None:
    engine = _engine()
    state = engine.new_game(game_id="city-supply")
    engine.save_player_command(
        state,
        "move weapons to Hanzhong",
        "logistics",
        orders=[
            AgentOrder(
                unit_id="liu_bei_caravan_1",
                action="transfer",
                source_city_id="chengdu",
                target_city_id="hanzhong",
                resource="weapons",
                amount=16,
            )
        ],
    )

    engine.resolve_round(state)

    assert state.city_supply["hanzhong"]["weapons"] >= 16
    assert state.regional_supply["hanzhong"]["weapons"] >= 16


def test_city_capture_transfers_spoils_and_recolors_summary() -> None:
    engine = _engine()
    state = engine.new_game(game_id="city-spoils")
    state.round = 1
    state.city_supply["tianshui"] = {"food": 40, "weapons": 20, "gold": 10}
    unit = next(unit for unit in state.units if unit.general_id == "ma_chao")
    unit.soldiers = 120000
    unit.max_soldiers = 130000
    before_food = state.resources["liu_bei"].food
    before_manpower = state.resources["liu_bei"].manpower

    won = engine._resolve_city_battle(state, unit, "wudu", "tianshui", "cao", {})
    state.region_owners = engine._derive_region_owners_from_cities(state.city_owners, state.regions)

    assert won
    assert state.city_owners["tianshui"] == "liu_bei"
    assert state.resources["liu_bei"].food >= before_food + 26
    assert state.resources["liu_bei"].manpower > before_manpower
    assert state.city_supply["tianshui"]["weapons"] == 7
    assert any("Spoils" in event.summary for event in state.battle_events)
