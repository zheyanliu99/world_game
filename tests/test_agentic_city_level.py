from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

from hwsim.agentic.agents import MockAgentProvider
from hwsim.agentic.city_data import load_city_graph, load_general_seeds
from hwsim.agentic.models import AgentOrder, AgenticAlliance, Resources
from hwsim.agentic.simulator import AgenticGameEngine


ROOT = Path(__file__).resolve().parents[1]


def _engine() -> AgenticGameEngine:
    return AgenticGameEngine.from_default_scenario(agent_provider=MockAgentProvider(), fallback_provider=MockAgentProvider())


def test_city_graph_and_general_seed_validate() -> None:
    graph = load_city_graph()
    engine = _engine()
    seeds = load_general_seeds(city_ids=set(graph.city_map()))

    assert engine.real_map_view is not None
    assert graph.canvas_size == engine.real_map_view.canvas_size
    assert 45 <= len(graph.cities) <= 52
    assert any(city.name_cn == "建业" for city in graph.cities)
    width, height = graph.canvas_size
    assert all(0 <= city.position[0] <= width and 0 <= city.position[1] <= height for city in graph.cities)
    assert any(seed.name_cn == "关羽" and seed.soldiers > 10000 for seed in seeds)
    assert len([seed for seed in seeds if seed.faction_id == "cao"]) >= 36
    assert len([seed for seed in seeds if seed.faction_id == "liu_bei"]) >= 30
    assert len([seed for seed in seeds if seed.faction_id == "sun_quan"]) >= 30
    assert next(seed for seed in seeds if seed.id == "cao_cao").soldiers == 35000
    assert next(seed for seed in seeds if seed.id == "xiahou_dun").max_soldiers == 32500
    assert all((ROOT / "src/hwsim/web/static" / seed.portrait_path.removeprefix("/static/")).exists() for seed in seeds)


def test_road_graph_derives_city_neighbors_and_costs() -> None:
    graph = load_city_graph()
    city_ids = set(graph.city_map())
    road_keys = {tuple(sorted((road.from_city_id, road.to_city_id))) for road in graph.roads}

    assert len(graph.roads) >= 80
    assert {"shu_road", "mountain_pass", "river", "plain_road"} <= {road.route_type for road in graph.roads}
    assert tuple(sorted(("chengdu", "wudu"))) not in road_keys
    assert tuple(sorted(("chengdu", "hanzhong"))) in road_keys
    for city in graph.cities:
        assert city.id in city_ids
        assert city.neighbors
        for neighbor in city.neighbors:
            assert tuple(sorted((city.id, neighbor))) in road_keys


def test_bigquery_schema_matches_csv_seed() -> None:
    schema = json.loads((ROOT / "data/generals/general_seed_bigquery_schema.json").read_text(encoding="utf-8"))
    with (ROOT / "data/generals/general_seed.csv").open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert reader.fieldnames == [field["name"] for field in schema]
    assert len(rows) >= 96
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
    assert len([general for general in state.generals.values() if general.faction_id == "cao"]) == 12
    assert sum(1 for general in state.generals.values() if general.faction_id in {"liu_bei", "sun_quan", "cao"}) == 27
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


def test_move_action_consumes_route_cost_and_updates_city() -> None:
    engine = _engine()
    state = engine.new_game(game_id="move-cost")
    unit = next(unit for unit in state.units if unit.general_id == "ma_chao")
    road = engine._road_between("wudu", "hanzhong")
    assert road is not None
    before_soldiers = unit.soldiers
    before_readiness = unit.readiness
    before_food = state.resources["liu_bei"].food
    before_gold = state.resources["liu_bei"].gold

    ok = engine._execute_order(
        state,
        "liu_bei",
        unit,
        AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="move", source_city_id="wudu", target_city_id="hanzhong"),
        {},
        [],
    )

    assert ok
    assert unit.city_id == "hanzhong"
    assert state.resources["liu_bei"].food == before_food - road.food_cost
    assert state.resources["liu_bei"].gold == before_gold - road.gold_cost
    assert unit.readiness == before_readiness - road.readiness_cost
    assert unit.soldiers == before_soldiers - int(before_soldiers * road.soldier_loss_bps / 10000)
    assert any(log.title == "行军消耗" and "武都→汉中" in log.detail for log in state.logs)


def test_move_action_requires_direct_road_and_resources() -> None:
    engine = _engine()
    state = engine.new_game(game_id="move-reject")
    unit = next(unit for unit in state.units if unit.general_id == "zhao_yun")
    state.resources["liu_bei"] = Resources(food=0, weapons=78, gold=0, manpower=80, intel=14)

    impossible = engine._execute_order(
        state,
        "liu_bei",
        unit,
        AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="move", source_city_id="chengdu", target_city_id="wudu"),
        {},
        [],
    )
    costly = engine._execute_order(
        state,
        "liu_bei",
        unit,
        AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="move", source_city_id="chengdu", target_city_id="hanzhong"),
        {},
        [],
    )

    assert not impossible
    assert not costly
    assert unit.city_id == "chengdu"
    assert any("没有直达道路" in log.detail for log in state.logs)
    assert any("缺少" in log.detail for log in state.logs)


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

    battle = next(item for item in state.active_battles if item.target_city_id == "tianshui")
    assert battle.attacker_unit_ids
    assert 2 <= battle.duration_rounds <= 10
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
    assert any("缴获" in event.summary for event in state.battle_events)


def test_stranded_enemy_city_army_must_resolve() -> None:
    engine = _engine()
    state = engine.new_game(game_id="stranded")
    unit = next(unit for unit in state.units if unit.general_id == "ma_chao")
    unit.city_id = "xuchang"
    unit.region_id = "yanzhou"
    state.city_owners["xuchang"] = "cao"

    engine._resolve_stranded_units(state)

    assert not (
        unit in state.units
        and unit.city_id == "xuchang"
        and state.city_owners[unit.city_id] != unit.faction_id
        and not engine._are_allied(state, unit.faction_id, state.city_owners[unit.city_id])
    )
    assert any(log.title in {"孤军撤退", "孤军降服", "孤军覆灭"} for log in state.logs)


def test_active_battle_duration_and_reinforcement() -> None:
    engine = _engine()
    state = engine.new_game(game_id="active-battle")
    attacker = next(unit for unit in state.units if unit.general_id == "ma_chao")
    engine.save_player_command(
        state,
        "attack Tianshui",
        "war",
        orders=[AgentOrder(unit_id=attacker.id, general_id=attacker.general_id, action="attack", source_city_id="wudu", target_city_ids=["tianshui"])],
    )

    engine.resolve_round(state)

    battle = next(item for item in state.active_battles if item.target_city_id == "tianshui")
    assert 2 <= battle.duration_rounds <= 10
    assert state.city_owners["tianshui"] == "cao"

    zhang_fei = next(unit for unit in state.units if unit.general_id == "zhang_fei")
    zhang_fei.city_id = "wudu"
    zhang_fei.region_id = "hanzhong"
    engine.save_player_command(
        state,
        "reinforce the battle",
        "war",
        orders=[AgentOrder(unit_id=zhang_fei.id, general_id=zhang_fei.general_id, action="reinforce", battle_id=battle.id, source_city_id="wudu", target_city_id="tianshui")],
    )
    engine.resolve_round(state)

    updated = next(item for item in state.active_battles if item.id == battle.id)
    assert zhang_fei.id in updated.attacker_unit_ids
    assert sum(state.city_supply["tianshui"].get(resource, 0) for resource in ("food", "weapons", "gold")) > 0


def test_city_stack_uses_historical_leader_priority() -> None:
    engine = _engine()
    state = engine.new_game(game_id="stack-leader")
    state.generals["zhuge_liang"].city_id = "chengdu"
    stack = next(stack for stack in engine._city_stacks(state) if stack.city_id == "chengdu" and stack.faction_id == "liu_bei")

    assert stack.leader_general_id == "zhuge_liang"
    assert stack.general_count >= 5
    assert stack.total_soldiers >= 69000
    assert stack.power_score > stack.total_soldiers ** 0.5


def test_advisor_recommendations_use_legal_city_roads() -> None:
    engine = _engine()
    state = engine.new_game(game_id="advisor-roads")
    recommendation = engine.recommend_player_plan(state)
    units = {unit.id: unit for unit in state.units}

    assert recommendation.policy
    assert recommendation.orders
    for order in recommendation.orders:
        unit = units.get(order.unit_id or "")
        if not unit or order.action in {"rest", "defend", "farm", "retreat"}:
            continue
        source = order.source_city_id or unit.city_id
        if order.action in {"move", "scout", "transfer", "reinforce"}:
            target = order.target_city_id or (order.target_city_ids[0] if order.target_city_ids else None)
            assert target is None or target == source or engine._road_between(source or "", target)
        if order.action == "attack":
            assert order.source_city_id
            assert not order.target_region_id
            current = order.source_city_id
            for target in order.target_city_ids:
                assert engine._road_between(current, target)
                current = target


def test_incidents_and_general_discovery_are_deterministic() -> None:
    engine = _engine()
    first = engine.new_game(game_id="one")
    second = engine.new_game(game_id="two")
    for state in (first, second):
        state.round = 6
        engine._apply_random_incidents(state)
        for unit in list(state.units):
            if unit.faction_id == "liu_bei" and unit.unit_type == "army" and unit.general_id not in {"guan_yu", "zhang_fei"}:
                unit.soldiers = 0
        engine._remove_dead_armies(state)
        engine._discover_generals(state)

    assert [event.summary for event in first.incident_events] == [event.summary for event in second.incident_events]
    assert [event.general_id for event in first.general_discovery_events] == [event.general_id for event in second.general_discovery_events]


def test_healthy_factions_do_not_discover_generals_too_early() -> None:
    engine = _engine()
    state = engine.new_game(game_id="slow-discovery")
    before = len(state.generals)
    state.round = 1

    engine._discover_generals(state)

    assert len(state.generals) == before
    assert state.general_discovery_events == []


def test_scout_intel_improves_attack_score() -> None:
    engine = _engine()
    state = engine.new_game(game_id="scout-score")
    unit = next(unit for unit in state.units if unit.general_id == "ma_chao")
    state.resources["liu_bei"].intel = 0
    baseline = engine._attack_score(state, "liu_bei", unit, "tianshui")
    state.resources["liu_bei"].intel = 24
    state.city_supply["tianshui"]["intel"] = 12

    improved = engine._attack_score(state, "liu_bei", unit, "tianshui")

    assert improved > baseline + 4


def test_general_death_and_surrender_are_broadcast() -> None:
    engine = _engine()
    state = engine.new_game(game_id="general-fate")
    unit = next(unit for unit in state.units if unit.general_id == "ma_chao")
    general = state.generals["ma_chao"]

    engine._kill_general(state, unit, general, "tianshui", "测试阵亡")
    assert any(log.title == "将领阵亡" and "马超" in log.detail for log in state.logs)

    defender = next(unit for unit in state.units if unit.general_id == "guan_yu")
    defender.faction_id = "cao"
    defender.city_id = "xinye"
    defender.region_id = "jingzhou"
    defender.soldiers = 2000
    state.generals["guan_yu"].faction_id = "cao"
    state.city_owners["xinye"] = "liu_bei"
    engine._broadcast_general_surrender(state, state.generals["guan_yu"], "cao", "liu_bei", "xinye", "测试归降")

    assert any(log.title == "将领归降" and "关羽" in log.detail for log in state.logs)
