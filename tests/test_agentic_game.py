from __future__ import annotations

from types import SimpleNamespace

import pytest

from hwsim.agentic.agents import (
    DeterministicAdvisorProvider,
    LocalCodexAdvisorError,
    LocalCodexAdvisorProvider,
    MockAgentProvider,
)
from hwsim.agentic.models import AdvisorRecommendation, AgentObservation, AgentOrder, AgentPlan, AgenticAlliance, DiplomacyOrder
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


def test_local_codex_advisor_provider_parses_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_CODEX_COMMAND", "/usr/bin/codex-fake")
    captured: dict[str, object] = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='{"policy":"defense","orders":[{"unit_id":"liu_bei_army_1","action":"defend"}],"diplomacy":[],"summary":"固守汉中。"}',
            stderr="",
        )

    engine = _engine()
    state = engine.new_game()
    observation = engine._observation_for(state, state.player_faction, 1)
    provider = LocalCodexAdvisorProvider(runner=runner, timeout_seconds=2)

    recommendation = provider.recommend(observation)

    assert recommendation.policy == "defense"
    assert recommendation.orders[0].unit_id == "liu_bei_army_1"
    command = captured["command"]
    kwargs = captured["kwargs"]
    assert "--model" in command
    assert "gpt-5.4-mini" in command
    assert "--sandbox" in command
    assert "read-only" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--skip-git-repo-check" in command
    assert "--cd" in command
    assert "--ask-for-approval" not in command
    assert kwargs["env"]["RUST_LOG"] == "error"
    assert "hwsim-codex-advisor-" in kwargs["cwd"]


def test_local_codex_advisor_schema_is_strict_for_codex() -> None:
    provider = LocalCodexAdvisorProvider(runner=lambda command, **kwargs: None)
    schema = provider._advisor_output_schema()

    assert schema["additionalProperties"] is False
    order_schema = schema["properties"]["orders"]["items"]
    diplomacy_schema = schema["properties"]["diplomacy"]["items"]
    assert order_schema["additionalProperties"] is False
    assert diplomacy_schema["additionalProperties"] is False
    assert "amount" in order_schema["required"]
    assert order_schema["properties"]["amount"]["type"] == "integer"


def test_local_codex_advisor_prompt_uses_compact_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_CODEX_ADVISOR_TIMEOUT", raising=False)
    engine = _engine()
    state = engine.new_game()
    observation = engine._observation_for(state, state.player_faction, 1)
    provider = LocalCodexAdvisorProvider(runner=lambda command, **kwargs: None)
    prompt = provider._prompt(observation)
    compact = provider._compact_observation(observation)

    assert provider.timeout_seconds == 90
    assert len(prompt) < len(observation.model_dump_json()) * 0.55
    assert "city_roads" not in prompt
    assert compact["units"]
    assert compact["roads"]


def test_local_codex_advisor_provider_rejects_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_CODEX_COMMAND", "/usr/bin/codex-fake")

    def runner(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="not json", stderr="")

    engine = _engine()
    state = engine.new_game()
    observation = engine._observation_for(state, state.player_faction, 1)
    provider = LocalCodexAdvisorProvider(runner=runner, timeout_seconds=2)

    with pytest.raises(LocalCodexAdvisorError):
        provider.recommend(observation)


def test_local_codex_advisor_uses_valid_output_despite_cli_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_CODEX_COMMAND", "/usr/bin/codex-fake")

    def runner(command, **kwargs):
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(
                '{"policy":"defense","orders":[{"unit_id":"liu_bei_army_1","action":"defend"}],'
                '"diplomacy":[],"summary":"虽有 CLI warning，仍采用合法 JSON。"}'
            )
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="2026-06-10T04:58:12Z WARN codex_rollout::list: state db discrepancy during find_task",
        )

    engine = _engine()
    state = engine.new_game()
    observation = engine._observation_for(state, state.player_faction, 1)
    provider = LocalCodexAdvisorProvider(runner=runner, timeout_seconds=2)

    recommendation = provider.recommend(observation)

    assert recommendation.policy == "defense"
    assert recommendation.summary.startswith("虽有 CLI warning")


def test_local_codex_advisor_parses_json_from_cli_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_CODEX_COMMAND", "/usr/bin/codex-fake")

    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "OpenAI Codex v0.136.0-alpha.2\n"
                "--------\n"
                "user\n"
                '局势观察 JSON：{"faction_id":"liu_bei","round":1}\n'
                "assistant\n"
                '{"policy":"war","orders":[{"unit_id":"liu_bei_army_1","action":"defend"}],'
                '"diplomacy":[],"summary":"从 transcript 末尾提取军师 JSON。"}'
            ),
            stderr="",
        )

    engine = _engine()
    state = engine.new_game()
    observation = engine._observation_for(state, state.player_faction, 1)
    provider = LocalCodexAdvisorProvider(runner=runner, timeout_seconds=2)

    recommendation = provider.recommend(observation)

    assert recommendation.policy == "war"
    assert recommendation.summary == "从 transcript 末尾提取军师 JSON。"


def test_local_codex_advisor_filters_startup_warning_without_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_CODEX_COMMAND", "/usr/bin/codex-fake")

    def runner(command, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "2026-06-10T05:00:33.174940Z WARN codex_rollout::list: "
                "state db discrepancy during find_task\n"
                "2026-06-10T05:00:33.175101Z WARN codex_core_plugins::manifest: "
                "ignoring interface.defaultPrompt[0]: prompt must be at most 128 characters"
            ),
        )

    engine = _engine()
    state = engine.new_game()
    observation = engine._observation_for(state, state.player_faction, 1)
    provider = LocalCodexAdvisorProvider(runner=runner, timeout_seconds=2)

    with pytest.raises(LocalCodexAdvisorError) as exc_info:
        provider.recommend(observation)

    message = str(exc_info.value)
    assert "codex_rollout" not in message
    assert "defaultPrompt" not in message
    assert "没有生成合法军师 JSON" in message


def test_to_view_does_not_call_local_codex_provider() -> None:
    class CountingLocalProvider:
        def __init__(self) -> None:
            self.calls = 0

        def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
            self.calls += 1
            raise AssertionError("local Codex should be manual only")

    local_provider = CountingLocalProvider()
    engine = AgenticGameEngine.from_default_scenario(
        agent_provider=MockAgentProvider(),
        fallback_provider=MockAgentProvider(),
        local_codex_advisor_provider=local_provider,
    )
    state = engine.new_game()

    view = engine.to_view(state)

    assert local_provider.calls == 0
    assert view.advisor_source == "deterministic"
    assert view.advisor_error == ""


def test_local_codex_advisor_success_and_illegal_fallback() -> None:
    class LegalLocalProvider:
        def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
            return DeterministicAdvisorProvider().recommend(observation)

    engine = AgenticGameEngine.from_default_scenario(
        agent_provider=MockAgentProvider(),
        fallback_provider=MockAgentProvider(),
        local_codex_advisor_provider=LegalLocalProvider(),
    )
    state = engine.new_game()

    recommendation = engine.request_local_codex_advisor(state)

    assert recommendation.orders
    assert state.advisor_source == "local_codex"
    assert state.advisor_error == ""

    class IllegalLocalProvider:
        def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
            return AdvisorRecommendation(policy="war", orders=[AgentOrder(unit_id="cao_army_1", action="defend")], summary="bad")

    engine = AgenticGameEngine.from_default_scenario(
        agent_provider=MockAgentProvider(),
        fallback_provider=MockAgentProvider(),
        local_codex_advisor_provider=IllegalLocalProvider(),
    )
    state = engine.new_game()

    recommendation = engine.request_local_codex_advisor(state)

    assert recommendation.orders
    assert state.advisor_source == "local_codex_fallback"
    assert "不存在的蜀汉单位" in state.advisor_error


def test_local_codex_advisor_repairs_illegal_player_order() -> None:
    class PartlyIllegalLocalProvider:
        def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
            return AdvisorRecommendation(
                policy="war",
                orders=[
                    AgentOrder(
                        unit_id="liu_bei_army_5",
                        general_id="ma_chao",
                        action="move",
                        source_city_id="wudu",
                        target_city_id=None,
                    )
                ],
                summary="马超尝试移动。",
            )

    engine = AgenticGameEngine.from_default_scenario(
        agent_provider=MockAgentProvider(),
        fallback_provider=MockAgentProvider(),
        local_codex_advisor_provider=PartlyIllegalLocalProvider(),
    )
    state = engine.new_game()

    recommendation = engine.request_local_codex_advisor(state)

    assert state.advisor_source == "local_codex"
    assert state.advisor_error == ""
    assert recommendation.orders[0].unit_id == "liu_bei_army_5"
    assert recommendation.orders[0].action == "defend"
    assert "已自动改为固守" in recommendation.summary


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
