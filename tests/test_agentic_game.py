from __future__ import annotations

from hwsim.agentic.agents import MockAgentProvider
from hwsim.agentic.models import AgentObservation, AgentOrder, AgentPlan, AgenticAlliance, DiplomacyOrder
from hwsim.agentic.simulator import POLICY_MODIFIERS, AgenticGameEngine


def _engine(agent_provider=None) -> AgenticGameEngine:
    provider = agent_provider or MockAgentProvider()
    return AgenticGameEngine.from_default_scenario(agent_provider=provider, fallback_provider=MockAgentProvider())


def test_agentic_game_initializes_three_kingdoms() -> None:
    state = _engine().new_game(game_id="test")

    assert state.game_id == "test"
    assert state.player_faction == "liu_bei"
    assert state.round == 0
    assert state.max_rounds == 100
    assert set(state.factions) == {"cao", "liu_bei", "sun_quan"}
    assert len([region for region, owner in state.region_owners.items() if owner == "liu_bei"]) == 4
    assert any(unit.unit_type == "caravan" for unit in state.units if unit.faction_id == "liu_bei")


def test_mock_agentic_game_is_deterministic() -> None:
    engine = _engine()
    first = engine.new_game(game_id="a")
    second = engine.new_game(game_id="b")

    for state in (first, second):
        engine.save_player_command(state, "farm, scout, and defend", "farming")
        for _ in range(4):
            engine.resolve_round(state)

    assert first.region_owners == second.region_owners
    assert [(unit.id, unit.region_id, unit.readiness) for unit in first.units] == [
        (unit.id, unit.region_id, unit.readiness) for unit in second.units
    ]
    assert [log.title for log in first.logs] == [log.title for log in second.logs]


def test_round_cap_does_not_create_score_winner() -> None:
    engine = _engine()
    state = engine.new_game()
    state.max_rounds = 3

    for _ in range(5):
        engine.resolve_round(state)

    assert not state.finished
    assert state.round == 5
    assert state.winner is None


def test_victory_requires_eliminating_enemy_factions() -> None:
    engine = _engine()
    state = engine.new_game()
    for city_id in state.city_owners:
        state.city_owners[city_id] = "liu_bei"
    for unit in state.units:
        if unit.faction_id != "liu_bei":
            unit.soldiers = 0

    engine.resolve_round(state)

    assert state.finished
    assert state.winner == "liu_bei"


def test_policy_modifiers_cover_required_tradeoffs() -> None:
    assert POLICY_MODIFIERS["farming"]["food"] > POLICY_MODIFIERS["balanced"]["food"]
    assert POLICY_MODIFIERS["farming"]["attack"] < POLICY_MODIFIERS["balanced"]["attack"]
    assert POLICY_MODIFIERS["war"]["attack"] > POLICY_MODIFIERS["balanced"]["attack"]
    assert POLICY_MODIFIERS["war"]["food"] < POLICY_MODIFIERS["balanced"]["food"]
    assert POLICY_MODIFIERS["logistics"]["transfer"] > POLICY_MODIFIERS["balanced"]["transfer"]
    assert POLICY_MODIFIERS["defense"]["defense"] > POLICY_MODIFIERS["balanced"]["defense"]
    assert POLICY_MODIFIERS["diplomacy"]["gold"] > POLICY_MODIFIERS["balanced"]["gold"]


def test_farming_order_improves_region_and_food() -> None:
    engine = _engine()
    state = engine.new_game()
    before_food = state.resources["liu_bei"].food
    before_development = state.region_development["yizhou"]
    engine.save_player_command(
        state,
        "focus on farming",
        "farming",
        orders=[AgentOrder(unit_id="liu_bei_worker_1", action="farm", region_id="yizhou")],
    )

    engine.resolve_round(state)

    assert state.policies["liu_bei"] == "farming"
    assert state.region_development["yizhou"] > before_development
    assert state.resources["liu_bei"].food > before_food


def test_alliance_blocks_player_attack() -> None:
    engine = _engine()
    state = engine.new_game()
    state.alliances.append(AgenticAlliance(factions=("cao", "liu_bei"), expires_round=3))
    engine.save_player_command(
        state,
        "attack north",
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

    assert state.city_owners["tianshui"] == "cao"
    assert any("Alliance blocks" in log.detail for log in state.logs)


def test_each_general_can_act_once_without_faction_ap_cap() -> None:
    engine = _engine()
    state = engine.new_game()
    guan = next(unit for unit in state.units if unit.general_id == "guan_yu")
    guan.city_id = "xinye"
    guan.region_id = "jingzhou"
    engine.save_player_command(
        state,
        "attack on two fronts",
        "war",
        orders=[
            AgentOrder(unit_id="liu_bei_army_1", general_id="guan_yu", action="attack", source_city_id="xinye", target_city_ids=["xuchang"]),
            AgentOrder(unit_id="liu_bei_army_5", general_id="ma_chao", action="attack", source_city_id="wudu", target_city_ids=["tianshui"]),
        ],
    )

    engine.resolve_round(state)

    assert any(log.title == "战役爆发" and "许昌" in log.detail for log in state.logs)
    assert any(log.title == "战役爆发" and "天水" in log.detail for log in state.logs)
    assert not any("lacks AP" in log.detail for log in state.logs)


def test_attack_requires_and_spends_food() -> None:
    engine = _engine()
    state = engine.new_game()
    guan = next(unit for unit in state.units if unit.general_id == "guan_yu")
    guan.city_id = "xinye"
    guan.region_id = "jingzhou"
    state.resources["liu_bei"].food = 0
    state.resources["liu_bei"].gold = 0
    state.city_supply["xinye"] = {"food": 0, "gold": 0}
    engine.save_player_command(
        state,
        "attack without food",
        "war",
        orders=[AgentOrder(unit_id=guan.id, general_id="guan_yu", action="attack", source_city_id="xinye", target_city_ids=["xuchang"])],
    )

    engine.resolve_round(state)

    assert any(log.title == "行军被拒" and "缺少" in log.detail for log in state.logs)
    assert not any(log.title == "战役爆发" and "许昌" in log.detail for log in state.logs)


def test_gold_and_manpower_reinforce_armies() -> None:
    engine = _engine()
    state = engine.new_game()
    unit = next(unit for unit in state.units if unit.general_id == "zhao_yun")
    unit.soldiers = 10000
    state.resources["liu_bei"].food = 999
    state.resources["liu_bei"].gold = 80
    state.resources["liu_bei"].manpower = 50000

    engine._recover_units(state)

    assert unit.soldiers > 10000
    assert state.resources["liu_bei"].gold < 80
    assert any(log.title == "Recruitment" for log in state.logs)


def test_unordered_units_default_to_defense_and_improve_farms() -> None:
    engine = _engine()
    state = engine.new_game()
    before = state.city_development["chengdu"]
    engine.save_player_command(state, "", "balanced", orders=[])

    engine.resolve_round(state)

    assert state.city_development["chengdu"] > before
    assert any(unit.status == "defending" for unit in state.units if unit.faction_id == "liu_bei")


def test_advisor_recommendation_returns_legal_player_orders() -> None:
    engine = _engine()
    state = engine.new_game()

    recommendation = engine.recommend_player_plan(state)
    player_units = {unit.id for unit in state.units if unit.faction_id == state.player_faction}

    assert recommendation.policy in POLICY_MODIFIERS
    assert recommendation.summary
    assert recommendation.orders
    assert {order.unit_id for order in recommendation.orders if order.unit_id} <= player_units
    assert len([order.unit_id for order in recommendation.orders if order.unit_id]) == len({order.unit_id for order in recommendation.orders if order.unit_id})


def test_transfer_adds_regional_supply() -> None:
    engine = _engine()
    state = engine.new_game()
    engine.save_player_command(
        state,
        "move weapons to Hanzhong",
        "logistics",
        orders=[
            AgentOrder(
                unit_id="liu_bei_caravan_1",
                action="transfer",
                target_region_id="hanzhong",
                resource="weapons",
                amount=16,
            )
        ],
    )

    engine.resolve_round(state)

    assert state.regional_supply["hanzhong"]["weapons"] >= 16


class BrokenProvider:
    def decide(self, observation: AgentObservation) -> AgentPlan:
        raise RuntimeError("model unavailable")


def test_agent_provider_failure_falls_back_to_mock() -> None:
    engine = _engine(agent_provider=BrokenProvider())
    state = engine.new_game()

    engine.resolve_round(state)

    assert state.round == 1
    assert state.last_plans["cao"].reasoning_summary
    assert any(log.title == "AI fallback" for log in state.logs)


def test_weighted_wei_alliance_proposal_has_ten_round_outcome() -> None:
    engine = _engine()
    state = engine.new_game(game_id="wei-diplomacy")
    engine.save_player_command(
        state,
        "联魏换取喘息",
        "diplomacy",
        diplomacy=[DiplomacyOrder(type="propose_alliance", target="cao", duration_rounds=1)],
    )

    engine.resolve_round(state)

    assert any(log.title in {"盟约缔结", "结盟受阻"} and ("曹魏" in log.detail or log.faction_id == "cao") for log in state.logs)
    if state.alliances:
        assert all(alliance.expires_round - state.round == 10 for alliance in state.alliances if "cao" in alliance.factions)
