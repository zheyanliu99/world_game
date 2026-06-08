from hwsim.core.game_state import faction_ranking
from hwsim.core.simulator import Simulator
from hwsim.map.map_loader import load_bundle


def test_simulation_is_deterministic_and_reaches_demo_arc() -> None:
    scenario, map_config, event_config, _style = load_bundle(
        "configs/scenarios/sanguo_shu_unification_demo.json"
    )

    first = Simulator(scenario, map_config, event_config).run()
    second = Simulator(scenario, map_config, event_config).run()

    assert first.final_state.region_owners == second.final_state.region_owners
    assert [event.event_id for event in first.triggered_events] == [
        "yellow_turban_184",
        "guandu_200",
        "chibi_208",
        "liu_bei_enters_shu_214",
        "cao_pi_emperor_220",
        "yiling_222",
        "northern_expeditions_227",
        "gaopingling_249",
        "shu_unification_263",
    ]
    assert faction_ranking(first.final_state)[0] == ("liu_bei", 16)

