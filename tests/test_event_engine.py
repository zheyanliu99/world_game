from hwsim.core.event_engine import EventEngine
from hwsim.core.game_state import build_initial_state
from hwsim.core.models import TemporaryModifier
from hwsim.map.map_loader import load_bundle


def test_chibi_effects_create_expected_modifiers_and_alliance() -> None:
    scenario, map_config, event_config, _style = load_bundle(
        "configs/scenarios/sanguo_shu_unification_demo.json"
    )
    state = build_initial_state(scenario, map_config)
    state.current_year = 208
    cao_troops = state.factions["cao"].troops

    triggered = EventEngine(event_config).trigger_year_start_events(state)

    assert [event.event_id for event in triggered] == ["chibi_208"]
    assert state.factions["cao"].troops == int(cao_troops * 0.62)
    assert any(modifier.target == "liu_bei" and modifier.stat == "expansion" for modifier in state.active_modifiers)
    assert any(alliance.includes_pair("liu_bei", "sun_quan") for alliance in state.alliances)


def test_caopi_rename_changes_cao_display_name() -> None:
    scenario, map_config, event_config, _style = load_bundle(
        "configs/scenarios/sanguo_shu_unification_demo.json"
    )
    state = build_initial_state(scenario, map_config)
    state.current_year = 220

    EventEngine(event_config).trigger_year_start_events(state)

    assert state.factions["cao"].display_name(220) == "曹魏"


def test_temporary_modifier_expiration() -> None:
    scenario, map_config, event_config, _style = load_bundle(
        "configs/scenarios/sanguo_shu_unification_demo.json"
    )
    state = build_initial_state(scenario, map_config)
    engine = EventEngine(event_config)
    state.active_modifiers.append(
        TemporaryModifier(
            source_event="test",
            target="cao",
            stat="attack",
            multiplier=1.5,
            expires_year=210,
        )
    )

    state.current_year = 209
    engine.expire_year_end_effects(state)
    assert state.active_modifiers

    state.current_year = 210
    engine.expire_year_end_effects(state)
    assert state.active_modifiers == []

