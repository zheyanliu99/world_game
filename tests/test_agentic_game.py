from __future__ import annotations

from hwsim.agentic.agents import MockAgentProvider
from hwsim.agentic.models import AgentObservation, AgentOrder, AgentPlan, AgenticAlliance, DiplomacyOrder
from hwsim.agentic.simulator import ACTION_POINTS, POLICY_MODIFIERS, AgenticGameEngine


def _engine(agent_provider=None) -> AgenticGameEngine:
    provider = agent_provider or MockAgentProvider()
    return AgenticGameEngine.from_default_scenario(agent_provider=provider, fallback_provider=MockAgentProvider())


def test_agentic_game_initializes_three_kingdoms() -> None:
    state = _engine().new_game(game_id="test")

    assert state.game_id == "test"
    assert state.player_faction == "liu_bei"
    assert state.round == 0
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


def test_game_finishes_no_later_than_round_20() -> None:
    engine = _engine()
    state = engine.new_game()

    for _ in range(25):
        engine.resolve_round(state)

    assert state.finished
    assert state.round <= 20
    assert state.winner in {"cao", "liu_bei", "sun_quan"}


def test_early_dominance_victory() -> None:
    engine = _engine()
    state = engine.new_game()
    for index, region_id in enumerate(state.region_owners):
        state.region_owners[region_id] = "liu_bei" if index < 12 else "cao"

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
        orders=[AgentOrder(unit_id="liu_bei_army_1", action="attack", target_region_id="sili")],
    )

    engine.resolve_round(state)

    assert state.region_owners["sili"] == "cao"
    assert any("Alliance blocks" in log.detail for log in state.logs)


def test_action_point_budget_limits_orders() -> None:
    engine = _engine()
    state = engine.new_game()
    engine.save_player_command(
        state,
        "ally Wu and attack on two fronts",
        "war",
        orders=[
            AgentOrder(unit_id="liu_bei_army_1", action="attack", target_region_id="sili"),
            AgentOrder(unit_id="liu_bei_army_2", action="attack", target_region_id="yuzhou"),
        ],
        diplomacy=[DiplomacyOrder(type="propose_alliance", target="sun_quan", duration_rounds=5)],
    )

    engine.resolve_round(state)

    assert ACTION_POINTS == 5
    assert any("lacks AP" in log.detail for log in state.logs)


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
