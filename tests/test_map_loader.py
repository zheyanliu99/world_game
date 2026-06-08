from hwsim.map.adjacency import validate_symmetric_adjacency
from hwsim.map.map_loader import load_bundle


def test_loads_scenario_map_events_and_style() -> None:
    scenario, map_config, event_config, style = load_bundle(
        "configs/scenarios/sanguo_shu_unification_demo.json"
    )

    assert scenario.scenario_id == "sanguo_shu_unification_demo"
    assert len(map_config.regions) >= 12
    assert len(event_config.events) >= 9
    assert style.canvas_size == (1920, 1080)
    assert scenario.factions["liu_bei"].name_cn == "刘备"


def test_map_adjacency_is_valid_and_symmetric() -> None:
    scenario, map_config, _event_config, _style = load_bundle(
        "configs/scenarios/sanguo_shu_unification_demo.json"
    )
    regions = {region.id: region for region in map_config.regions}
    faction_ids = set(scenario.factions)

    assert all(region.initial_owner in faction_ids for region in regions.values())
    assert validate_symmetric_adjacency(regions) == []

