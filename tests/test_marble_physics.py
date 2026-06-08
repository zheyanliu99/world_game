from pathlib import Path
import random

import numpy as np

from hwsim.core.models import EventConfig, Faction, HistoricalEvent
from hwsim.physics.models import MarbleScenarioConfig, MarbleUnit, PhysicsConfig
from hwsim.physics.simulator import MarbleSimulator, load_marble_scenario


def _faction(faction_id: str, color: str) -> Faction:
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
    marble = MarbleUnit(id=1, faction_id="cao", x=3, y=60, vx=-80, vy=0, radius=4)
    sim.state.marbles = [marble]

    sim._move_marbles()

    assert marble.vx > 0


def test_water_boundary_bounce_reverses_outward_velocity() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    marble = MarbleUnit(id=1, faction_id="cao", x=22, y=60, vx=-80, vy=0, radius=4)
    sim.state.marbles = [marble]

    sim._move_marbles()

    assert marble.vx > 0


def test_enemy_frontier_bounces_and_captures() -> None:
    sim = MarbleSimulator(_scenario(), _prepared_map(), EventConfig(events=[]))
    sim.rng = random.Random(1)
    marble = MarbleUnit(id=1, faction_id="cao", x=55, y=60, vx=80, vy=0, radius=4, power=2.0)
    sim.state.marbles = [marble]
    before = int((sim.state.grid.owner_grid == sim.state.grid.faction_index("cao")).sum())

    sim._move_marbles()

    after = int((sim.state.grid.owner_grid == sim.state.grid.faction_index("cao")).sum())
    assert marble.vx < 0
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


def test_default_marble_scenario_is_three_kingdoms_only() -> None:
    scenario = load_marble_scenario("configs/scenarios/sanguo_marble_real_map_demo.json")

    assert set(scenario.factions) == {"cao", "liu_bei", "sun_quan"}
    assert scenario.physics.base_radius <= 3
    assert scenario.physics.capture_radius <= 9
