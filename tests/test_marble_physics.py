from pathlib import Path
import random

import numpy as np

from hwsim.core.models import Alliance, EventConfig, Faction, HistoricalEvent, StyleConfig, TriggeredEvent
from hwsim.game.marble_renderer import MarbleRenderer
from hwsim.physics.models import MarbleScenarioConfig, MarbleUnit, PhysicsConfig
from hwsim.physics.simulator import MarbleSimulator, load_marble_scenario


def _faction(
    faction_id: str,
    color: str,
    population: int | None = None,
    population_weight: float | None = None,
    capital: str | None = None,
) -> Faction:
    return Faction(
        id=faction_id,
        name_cn=faction_id,
        color=color,
        troops=10_000,
        morale=70,
        economy=70,
        stability=70,
        legitimacy=50,
        attack=1,
        defense=1,
        naval=1,
        expansion=1,
        population=population,
        population_spawn_weight=population_weight,
        capital_region=capital,
    )


def _scenario(seed: int = 7) -> MarbleScenarioConfig:
    return MarbleScenarioConfig(
        scenario_id="tiny_marble",
        title="tiny",
        start_year=184,
        end_year=280,
        video_length_seconds=4,
        render_fps=60,
        output_fps=60,
        random_seed=seed,
        real_map_file=Path("unused.json"),
        event_file=Path("unused.json"),
        style_file=Path("configs/styles/default_style.json"),
        intro_narration="intro",
        physics=PhysicsConfig(
            fps=60,
            substeps=1,
            min_speed=60,
            max_speed=60,
            capture_radius=8,
            base_radius=4,
            initial_marbles_per_faction=1,
            spawn_interval_frames=10,
            spawn_cost=25,
            max_marbles_per_faction=4,
        ),
        factions={"cao": _faction("cao", "#336699"), "liu_bei": _faction("liu_bei", "#66AA44")},
    )


def _prepared_map() -> dict:
    province_grid = np.full((12, 12), -1, dtype=int)
    province_grid[2:10, 2:6] = 0
    province_grid[2:10, 6:10] = 1
    return {
        "canvas_size": [120, 120],
        "grid_size": [12, 12],
        "attribution": "fixture",
        "provinces": [
            {"id": 0, "name": "Alpha", "owner": "cao", "centroid": [40, 60], "cell_count": 32},
            {"id": 1, "name": "Beta", "owner": "liu_bei", "centroid": [80, 60], "cell_count": 32},
        ],
        "state_regions": [
            {"id": "alpha_state", "name_cn": "甲州", "contains": ["Alpha"], "population_weight": 1.0},
            {"id": "beta_state", "name_cn": "乙州", "contains": ["Beta"], "population_weight": 1.0},
        ],
        "province_id_grid": province_grid.tolist(),
    }


def _three_kingdom_prepared_map() -> dict:
    province_grid = np.full((12, 18), -1, dtype=int)
    province_grid[1:11, 1:7] = 0
    province_grid[1:11, 7:12] = 1
    province_grid[1:11, 12:17] = 2
    return {
        "canvas_size": [180, 120],
        "grid_size": [18, 12],
        "attribution": "fixture",
        "provinces": [
            {"id": 0, "name": "Sichuan Province", "owner": "liu_bei", "centroid": [40, 60], "cell_count": 60},
            {"id": 1, "name": "Henan Province", "owner": "cao", "centroid": [95, 55], "cell_count": 50},
            {"id": 2, "name": "Jiangsu Province", "owner": "sun_quan", "centroid": [145, 62], "cell_count": 50},
        ],
        "state_regions": [
            {"id": "yizhou", "name_cn": "益州", "contains": ["Sichuan"], "population_weight": 1.0},
            {"id": "sili", "name_cn": "司隶", "contains": ["Henan"], "population_weight": 1.0},
            {"id": "yangzhou", "name_cn": "扬州", "contains": ["Jiangsu"], "population_weight": 1.0},
        ],
        "province_id_grid": province_grid.tolist(),
    }


def test_capture_only_changes_land_cells() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    sim.state.marbles = [
        MarbleUnit(id=1, faction_id="cao", x=62, y=60, vx=0, vy=0, radius=4, power=2.0)
    ]
    water_before = int((sim.state.grid.owner_grid < 0).sum())

    sim._capture_cells()

    assert int((sim.state.grid.owner_grid < 0).sum()) == water_before
    assert (sim.state.grid.owner_grid == sim.state.grid.faction_index("cao")).sum() > 32


def test_boundary_bounce_reverses_outward_velocity() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    marble = MarbleUnit(id=1, faction_id="cao", x=4.1, y=60, vx=-80, vy=0, radius=4)
    sim.state.marbles = [marble]

    sim._move_marbles()

    assert marble.vx > 0
    assert marble.x == marble.radius


def test_water_boundary_bounce_reverses_outward_velocity() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    marble = MarbleUnit(id=1, faction_id="cao", x=22, y=60, vx=-80, vy=0, radius=4)
    sim.state.marbles = [marble]

    sim._move_marbles()

    assert marble.vx > 0
    assert marble.x == 22


def test_enemy_frontier_bounces_and_captures() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    sim.rng = random.Random(1)
    marble = MarbleUnit(id=1, faction_id="cao", x=55, y=60, vx=80, vy=0, radius=4, power=2.0)
    sim.state.marbles = [marble]
    before = int((sim.state.grid.owner_grid == sim.state.grid.faction_index("cao")).sum())

    sim._move_marbles()

    after = int((sim.state.grid.owner_grid == sim.state.grid.faction_index("cao")).sum())
    assert marble.vx < 0
    assert marble.x == 55
    assert sim.state.grid.owner_index_at_canvas(marble.x, marble.y) == sim.state.grid.faction_index("cao")
    assert after > before


def test_marble_simulation_is_seed_deterministic() -> None:
    first = MarbleSimulator(_scenario(seed=11), _prepared_map(), EventConfig(events=[]))
    second = MarbleSimulator(_scenario(seed=11), _prepared_map(), EventConfig(events=[]))

    for _ in range(80):
        first.step()
        second.step()

    assert np.array_equal(first.state.grid.owner_grid, second.state.grid.owner_grid)
    assert [(round(m.x, 3), round(m.y, 3), m.faction_id) for m in first.state.marbles] == [
        (round(m.x, 3), round(m.y, 3), m.faction_id) for m in second.state.marbles
    ]


def test_marble_event_modifier_is_applied() -> None:
    event = HistoricalEvent.model_validate(
        {
            "id": "boost_184",
            "year": 184,
            "name_cn": "boost",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [
                {"type": "marble_modifier", "target": "cao", "stat": "speed", "multiplier": 1.5, "duration_years": 5}
            ],
            "ui": {"title": "boost", "subtitle": "boost", "duration_seconds": 1},
            "narration": "boost",
        }
    )
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[event]))

    sim.step()

    assert sim.state.stat_multiplier("cao", "speed") == 1.5
    assert sim.state.triggered_events[0].event_id == "boost_184"


def test_speed_modifier_moves_existing_marbles_faster() -> None:
    event = HistoricalEvent.model_validate(
        {
            "id": "boost_184",
            "year": 184,
            "name_cn": "boost",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [
                {"type": "marble_modifier", "target": "cao", "stat": "speed", "multiplier": 2.0, "duration_years": 5}
            ],
            "ui": {"title": "boost", "subtitle": "boost", "duration_seconds": 1},
            "narration": "boost",
        }
    )
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[event]))
    marble = MarbleUnit(id=1, faction_id="cao", x=40, y=60, vx=60, vy=0, radius=2)
    sim.state.marbles = [marble]
    sim.state.frame = 1

    sim.step()

    assert marble.x == 42


def test_strategic_bounce_steers_toward_target_after_wall_hit() -> None:
    scenario = _scenario()
    scenario.physics.strategic_bounce_strength = 0.75
    scenario.strategic_targets = {"cao": "liu_bei"}
    sim = MarbleSimulator(scenario, _prepared_map(), EventConfig(events=[]))
    marble = MarbleUnit(id=1, faction_id="cao", x=30, y=60, vx=-60, vy=-60, radius=2)
    sim.state.marbles = [marble]

    sim._reflect_axes(marble, reflect_x=False, reflect_y=True, steer=True)

    assert marble.vx > 0


def test_marble_event_add_balls_increases_unit_count() -> None:
    event = HistoricalEvent.model_validate(
        {
            "id": "reinforce_184",
            "year": 184,
            "name_cn": "reinforce",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [{"type": "add_balls", "target": "cao", "value": 2}],
            "ui": {"title": "reinforce", "subtitle": "reinforce", "duration_seconds": 1},
            "narration": "reinforce",
        }
    )
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[event]))
    sim.state.frame = 1
    before = len([marble for marble in sim.state.marbles if marble.faction_id == "cao"])

    sim.step()

    after = len([marble for marble in sim.state.marbles if marble.faction_id == "cao"])
    assert after == before + 2


def test_region_add_balls_spawn_near_event_center() -> None:
    event = HistoricalEvent.model_validate(
        {
            "id": "local_reinforce_184",
            "year": 184,
            "name_cn": "local",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [{"type": "add_balls", "target": "cao", "value": 3, "region": "Alpha"}],
            "ui": {"title": "local", "subtitle": "local", "duration_seconds": 1},
            "narration": "local",
        }
    )
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[event]))
    sim.state.marbles = []
    sim.state.frame = 1

    sim.step()

    spawned = [marble for marble in sim.state.marbles if marble.faction_id == "cao"]
    assert len(spawned) == 3
    assert all(marble.x < 65 for marble in spawned)


def test_betrayal_incident_converts_nearby_land_and_balls() -> None:
    event = HistoricalEvent.model_validate(
        {
            "id": "betray_184",
            "year": 184,
            "name_cn": "betray",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [
                {
                    "type": "betrayal",
                    "target": "cao",
                    "owner": "liu_bei",
                    "region": "Alpha",
                    "radius": 28,
                    "ball_fraction": 1.0,
                }
            ],
            "ui": {"title": "betray", "subtitle": "betray", "duration_seconds": 1},
            "narration": "betray",
        }
    )
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[event]))
    sim.state.marbles = [MarbleUnit(id=1, faction_id="cao", x=40, y=60, vx=0, vy=0, radius=4)]
    before_cao_cells = int((sim.state.grid.owner_grid == sim.state.grid.faction_index("cao")).sum())

    sim.director.update(sim.state)

    after_cao_cells = int((sim.state.grid.owner_grid == sim.state.grid.faction_index("cao")).sum())
    assert after_cao_cells < before_cao_cells
    assert sim.state.marbles[0].faction_id == "liu_bei"


def test_alliance_prevents_land_capture_until_break_event() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    sim.state.alliances.append(Alliance(factions=("cao", "liu_bei"), expires_year=220, source_event="test"))
    marble = MarbleUnit(id=1, faction_id="cao", x=62, y=60, vx=0, vy=0, radius=4, power=2.0)
    sim.state.marbles = [marble]
    liu_index = sim.state.grid.faction_index("liu_bei")
    before = int((sim.state.grid.owner_grid == liu_index).sum())

    sim._capture_cells_for_marble(marble)

    assert int((sim.state.grid.owner_grid == liu_index).sum()) == before

    break_event = HistoricalEvent.model_validate(
        {
            "id": "break_184",
            "year": 184,
            "name_cn": "break",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [{"type": "break_alliance", "factions": ["cao", "liu_bei"]}],
            "ui": {"title": "break", "subtitle": "break", "duration_seconds": 1},
            "narration": "break",
        }
    )
    sim.director.events = [break_event]
    sim.director.update(sim.state)
    sim._capture_cells_for_marble(marble)

    assert int((sim.state.grid.owner_grid == liu_index).sum()) < before


def test_surrender_faction_event_converts_all_land_and_population() -> None:
    event = HistoricalEvent.model_validate(
        {
            "id": "surrender_184",
            "year": 184,
            "name_cn": "surrender",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [{"type": "surrender_faction", "target": "cao", "owner": "liu_bei", "value": 1.0}],
            "ui": {"title": "surrender", "subtitle": "surrender", "duration_seconds": 1},
            "narration": "surrender",
        }
    )
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[event]))
    sim.state.resources = {"cao": 100.0, "liu_bei": 20.0}
    sim.state.marbles = [MarbleUnit(id=1, faction_id="cao", x=40, y=60, vx=0, vy=0, radius=4)]

    sim.director.update(sim.state)

    cao = sim.state.grid.faction_index("cao")
    liu = sim.state.grid.faction_index("liu_bei")
    assert not bool((sim.state.grid.owner_grid == cao).any())
    assert int((sim.state.grid.owner_grid == liu).sum()) == int((sim.state.grid.owner_grid >= 0).sum())
    assert sim.state.marbles[0].faction_id == "liu_bei"
    assert sim.state.resources["cao"] == 0
    assert sim.state.resources["liu_bei"] == 120


def test_surrender_faction_event_converts_units_when_land_is_already_lost() -> None:
    event = HistoricalEvent.model_validate(
        {
            "id": "surrender_184",
            "year": 184,
            "name_cn": "surrender",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [{"type": "surrender_faction", "target": "cao", "owner": "liu_bei", "value": 1.0}],
            "ui": {"title": "surrender", "subtitle": "surrender", "duration_seconds": 1},
            "narration": "surrender",
        }
    )
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[event]))
    cao = sim.state.grid.faction_index("cao")
    liu = sim.state.grid.faction_index("liu_bei")
    sim.state.grid.owner_grid[sim.state.grid.owner_grid == cao] = liu
    sim.state.marbles = [MarbleUnit(id=1, faction_id="cao", x=40, y=60, vx=0, vy=0, radius=4)]

    sim.director.update(sim.state)

    assert sim.state.marbles[0].faction_id == "liu_bei"


def test_marble_event_modifiers_expire() -> None:
    event = HistoricalEvent.model_validate(
        {
            "id": "short_boost_184",
            "year": 184,
            "name_cn": "short boost",
            "category": "test",
            "importance": 1,
            "trigger_conditions": [],
            "effects": [
                {"type": "marble_modifier", "target": "cao", "stat": "speed", "multiplier": 1.5, "duration_years": 1},
                {"type": "marble_modifier", "target": "cao", "stat": "spawn_rate", "multiplier": 1.5, "duration_years": 1},
            ],
            "ui": {"title": "short", "subtitle": "short", "duration_seconds": 1},
            "narration": "short",
        }
    )
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[event]))

    sim.step()
    assert sim.state.stat_multiplier("cao", "speed") == 1.5
    assert sim.state.stat_multiplier("cao", "spawn_rate") == 1.5

    sim.step(frames=5)

    assert sim.state.stat_multiplier("cao", "speed") == 1.0
    assert sim.state.stat_multiplier("cao", "spawn_rate") == 1.0


def test_default_marble_scenario_has_three_factions_and_three_minute_export() -> None:
    scenario = load_marble_scenario("configs/scenarios/sanguo_marble_real_map_demo.json")

    assert set(scenario.factions) == {"cao", "liu_bei", "sun_quan"}
    assert scenario.physics.base_radius <= 1.8
    assert scenario.physics.capture_radius <= 6
    assert scenario.physics.strategic_bounce_strength > 0
    assert scenario.physics.min_land_share_to_capture == 0.1
    assert scenario.physics.partial_surrender_land_share == 0.2
    assert scenario.physics.whole_surrender_land_share == 0.1
    assert scenario.physics.minimum_land_cells_by_faction["sun_quan"] > 0
    assert scenario.physics.state_capture_population_loss_fraction > 0
    assert scenario.target_winner == "liu_bei"
    assert scenario.video_length_seconds == 180
    assert scenario.output_fps == 60
    assert scenario.render_fps == 60


def test_population_weights_scale_initial_and_max_units() -> None:
    scenario = load_marble_scenario("configs/scenarios/sanguo_marble_real_map_demo.json")
    sim = MarbleSimulator(scenario, _three_kingdom_prepared_map(), EventConfig(events=[]))

    initial_counts = {
        faction_id: sum(1 for marble in sim.state.marbles if marble.faction_id == faction_id)
        for faction_id in scenario.factions
    }

    assert initial_counts["cao"] >= initial_counts["sun_quan"] > initial_counts["liu_bei"]
    start_caps = {faction_id: sim._max_marble_count(faction_id) for faction_id in scenario.factions}

    sim.state.frame = sim.state.total_frames
    end_caps = {faction_id: sim._max_marble_count(faction_id) for faction_id in scenario.factions}

    assert end_caps["cao"] > end_caps["sun_quan"] > end_caps["liu_bei"]
    assert sum(end_caps.values()) >= 700
    assert sum(start_caps.values()) < sum(end_caps.values())


def test_faction_below_ten_percent_cannot_capture_and_loses_population() -> None:
    scenario = _scenario()
    scenario.physics.min_land_share_to_capture = 0.1
    scenario.physics.collapse_resource_decay = 0.25
    scenario.physics.collapse_unit_loss_interval_frames = 1
    sim = MarbleSimulator(scenario, _prepared_map(), EventConfig(events=[]))
    cao = sim.state.grid.faction_index("cao")
    liu = sim.state.grid.faction_index("liu_bei")
    land = sim.state.grid.province_id_grid >= 0
    sim.state.grid.owner_grid[land] = liu
    sim.state.grid.owner_grid[2, 2] = cao
    sim.state.resources["cao"] = 100
    marble = MarbleUnit(id=99, faction_id="cao", x=62, y=60, vx=0, vy=0, radius=4, power=2.0)
    sim.state.marbles = [marble]

    sim._capture_cells_for_marble(marble)

    assert int((sim.state.grid.owner_grid == liu).sum()) == int(land.sum()) - 1

    sim._apply_collapse_pressure()

    assert sim.state.resources["cao"] == 75
    assert not sim.state.marbles


def test_low_land_share_triggers_yearly_partial_surrender_to_target_winner() -> None:
    scenario = _scenario()
    scenario.target_winner = "liu_bei"
    scenario.physics.partial_surrender_land_share = 0.2
    scenario.physics.partial_surrender_chance = 1.0
    scenario.physics.partial_surrender_fraction = 0.5
    scenario.physics.surrender_unit_fraction = 1.0
    sim = MarbleSimulator(scenario, _prepared_map(), EventConfig(events=[]))
    cao = sim.state.grid.faction_index("cao")
    liu = sim.state.grid.faction_index("liu_bei")
    land = sim.state.grid.province_id_grid >= 0
    sim.state.grid.owner_grid[land] = liu
    sim.state.grid.owner_grid[2:4, 2:4] = cao
    sim.state.resources["cao"] = 100
    sim.state.resources["liu_bei"] = 0
    sim.state.marbles = [MarbleUnit(id=1, faction_id="cao", x=25, y=25, vx=0, vy=0, radius=4)]

    before = int((sim.state.grid.owner_grid == cao).sum())
    sim._apply_land_share_surrenders()

    after = int((sim.state.grid.owner_grid == cao).sum())
    assert after < before
    converted = np.argwhere(sim.state.grid.owner_grid == cao)
    assert converted.size == 0 or converted[:, 1].max() - converted[:, 1].min() <= 1
    assert sim.state.marbles[0].faction_id == "liu_bei"
    assert sim.state.resources["cao"] < 100
    assert sim.state.resources["liu_bei"] > 0

    sim._apply_land_share_surrenders()

    assert int((sim.state.grid.owner_grid == cao).sum()) == after


def test_below_ten_percent_can_surrender_whole_country() -> None:
    scenario = _scenario()
    scenario.target_winner = "liu_bei"
    scenario.physics.whole_surrender_land_share = 0.1
    scenario.physics.whole_surrender_chance = 1.0
    sim = MarbleSimulator(scenario, _prepared_map(), EventConfig(events=[]))
    cao = sim.state.grid.faction_index("cao")
    liu = sim.state.grid.faction_index("liu_bei")
    land = sim.state.grid.province_id_grid >= 0
    sim.state.grid.owner_grid[land] = liu
    sim.state.grid.owner_grid[2, 2] = cao
    sim.state.resources["cao"] = 100

    sim._apply_land_share_surrenders()

    assert not bool((sim.state.grid.owner_grid == cao).any())
    assert int((sim.state.grid.owner_grid == liu).sum()) == int(land.sum())


def test_small_enclave_cleanup_merges_isolated_fragment() -> None:
    scenario = _scenario()
    scenario.physics.enclave_cleanup_interval_frames = 1
    scenario.physics.enclave_cleanup_max_cells = 4
    sim = MarbleSimulator(scenario, _prepared_map(), EventConfig(events=[]))
    cao = sim.state.grid.faction_index("cao")
    liu = sim.state.grid.faction_index("liu_bei")
    land = sim.state.grid.province_id_grid >= 0
    sim.state.grid.owner_grid[land] = liu
    sim.state.grid.owner_grid[5, 5] = cao

    sim._cleanup_small_enclaves()

    assert sim.state.grid.owner_grid[5, 5] == liu


def test_minimum_homeland_restores_wu_land_and_units() -> None:
    scenario = load_marble_scenario("configs/scenarios/sanguo_marble_real_map_demo.json")
    scenario.physics.minimum_land_cells_by_faction = {"sun_quan": 12}
    scenario.physics.minimum_units_by_faction = {"sun_quan": 2}
    scenario.physics.reserve_province_by_faction = {"sun_quan": "Jiangsu"}
    sim = MarbleSimulator(scenario, _three_kingdom_prepared_map(), EventConfig(events=[]))
    sun = sim.state.grid.faction_index("sun_quan")
    liu = sim.state.grid.faction_index("liu_bei")
    sim.state.grid.owner_grid[sim.state.grid.owner_grid == sun] = liu
    sim.state.marbles = [marble for marble in sim.state.marbles if marble.faction_id != "sun_quan"]

    sim._enforce_minimum_homelands()

    assert int((sim.state.grid.owner_grid == sun).sum()) == 12
    assert sum(1 for marble in sim.state.marbles if marble.faction_id == "sun_quan") == 2
    assert all(marble.x > 100 for marble in sim.state.marbles if marble.faction_id == "sun_quan")


def test_state_capture_transfers_population_to_winner() -> None:
    scenario = _scenario()
    scenario.physics.state_capture_population_loss_fraction = 0.25
    scenario.physics.state_capture_unit_fraction = 0.5
    sim = MarbleSimulator(scenario, _prepared_map(), EventConfig(events=[]))
    sim.state.resources = {"cao": 200.0, "liu_bei": 50.0}
    sim.state.state_controllers = {"alpha_state": "cao"}
    liu = sim.state.grid.faction_index("liu_bei")
    sim.state.grid.owner_grid[sim.state.grid.state_id_grid == 0] = liu
    sim.state.marbles = [
        MarbleUnit(id=1, faction_id="cao", x=40, y=60, vx=0, vy=0, radius=4),
        MarbleUnit(id=2, faction_id="cao", x=45, y=60, vx=0, vy=0, radius=4),
    ]

    sim._update_state_control_transfers()

    assert sim.state.state_controllers["alpha_state"] == "liu_bei"
    assert sim.state.resources["cao"] == 150
    assert sim.state.resources["liu_bei"] == 100
    assert sum(1 for marble in sim.state.marbles if marble.faction_id == "liu_bei") == 1


def test_state_control_adds_population_income() -> None:
    scenario = _scenario()
    scenario.physics.state_control_population_gain_per_100_cells = 20
    scenario.physics.spawn_cost = 10_000
    sim = MarbleSimulator(scenario, _prepared_map(), EventConfig(events=[]))
    sim.state.resources = {"cao": 0.0, "liu_bei": 0.0}

    sim._spawn_resources()

    assert sim.state.resources["cao"] > 0


def test_capital_labels_use_largest_owned_component_center() -> None:
    scenario = load_marble_scenario("configs/scenarios/sanguo_marble_real_map_demo.json")
    sim = MarbleSimulator(scenario, _three_kingdom_prepared_map(), EventConfig(events=[]))
    renderer = MarbleRenderer(StyleConfig())

    positions = renderer._capital_label_positions(sim.state)

    assert set(positions) == {"cao", "liu_bei", "sun_quan"}
    assert positions["cao"] == (95.0, 60.0)


def test_state_labels_have_configured_names() -> None:
    scenario = load_marble_scenario("configs/scenarios/sanguo_marble_real_map_demo.json")
    sim = MarbleSimulator(scenario, _three_kingdom_prepared_map(), EventConfig(events=[]))
    renderer = MarbleRenderer(StyleConfig())

    labels = renderer._state_label_positions(sim.state)

    assert labels["yizhou"][0] == "益州"
    assert labels["yangzhou"][0] == "扬州"


def test_capital_label_moves_to_largest_isolated_part() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    renderer = MarbleRenderer(StyleConfig())
    cao = sim.state.grid.faction_index("cao")
    liu = sim.state.grid.faction_index("liu_bei")
    land = sim.state.grid.province_id_grid >= 0
    sim.state.grid.owner_grid[land] = liu
    sim.state.grid.owner_grid[2, 2] = cao
    sim.state.grid.owner_grid[5:9, 3:6] = cao

    positions = renderer._capital_label_positions(sim.state)

    assert positions["cao"] == (45.0, 70.0)


def test_fire_overlay_does_not_draw_orange_map_blobs() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    sim.state.active_event = TriggeredEvent(
        event_id="fire",
        year=sim.state.current_year,
        name_cn="fire",
        title="fire",
        subtitle="fire",
        narration="fire",
        effect="fire_overlay",
        focus_regions=[],
        duration_seconds=1,
        importance=1,
    )
    renderer = MarbleRenderer(StyleConfig(canvas_size=sim.state.grid.canvas_size))

    image = renderer.render(sim.state)
    pixels = np.array(image)

    assert not bool(((pixels[:, :, 0] == 220) & (pixels[:, :, 1] == 75) & (pixels[:, :, 2] == 28)).any())
