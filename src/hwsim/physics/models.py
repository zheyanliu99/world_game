from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from hwsim.core.models import Alliance, Faction, TriggeredEvent


class PhysicsConfig(BaseModel):
    fps: int = 60
    substeps: int = 2
    min_speed: float = 72
    max_speed: float = 145
    bounce_jitter: float = 0.28
    capture_radius: float = 15
    base_radius: float = 5
    max_marbles_per_faction: int = 32
    spawn_interval_frames: int = 45
    spawn_cost: float = 55
    resource_gain_per_100_cells: float = 2.0
    initial_marbles_per_faction: int = 4
    collision_damage: float = 0.16


class MarbleScenarioConfig(BaseModel):
    simulation_mode: str = "marble"
    scenario_id: str
    title: str
    output_basename: str = "sanguo_marble_demo_001"
    start_year: int
    end_year: int
    video_length_seconds: float = 45
    render_fps: int = 60
    output_fps: int = 60
    target_winner: str | None = None
    historical_deviation: float = 0.7
    director_strength: str = "light"
    random_seed: int = 42
    real_map_file: Path
    event_file: Path
    style_file: Path
    intro_narration: str
    physics: PhysicsConfig = Field(default_factory=PhysicsConfig)
    factions: dict[str, Faction]


@dataclass
class MarbleUnit:
    id: int
    faction_id: str
    x: float
    y: float
    vx: float
    vy: float
    radius: float
    power: float = 1.0
    age_frames: int = 0
    cooldown_frames: int = 0


@dataclass
class MarbleModifier:
    target: str
    stat: str
    multiplier: float
    expires_frame: int
    source_event: str


@dataclass
class CellGrid:
    canvas_size: tuple[int, int]
    province_id_grid: np.ndarray
    owner_grid: np.ndarray
    faction_ids: list[str]
    province_records: list[dict]
    attribution: str = ""

    @property
    def grid_size(self) -> tuple[int, int]:
        return int(self.province_id_grid.shape[1]), int(self.province_id_grid.shape[0])

    @property
    def land_mask(self) -> np.ndarray:
        return self.province_id_grid >= 0

    @property
    def cell_size(self) -> tuple[float, float]:
        grid_w, grid_h = self.grid_size
        return self.canvas_size[0] / grid_w, self.canvas_size[1] / grid_h

    @classmethod
    def from_prepared_map(cls, prepared: dict, faction_ids: list[str]) -> CellGrid:
        province_grid = np.array(prepared["province_id_grid"], dtype=np.int16)
        owner_grid = np.full(province_grid.shape, -1, dtype=np.int16)
        faction_index = {faction_id: index for index, faction_id in enumerate(faction_ids)}
        for province in prepared["provinces"]:
            province_id = int(province["id"])
            owner = province.get("owner")
            owner_index = faction_index.get(owner, 0)
            owner_grid[province_grid == province_id] = owner_index
        owner_grid[province_grid < 0] = -1
        return cls(
            canvas_size=tuple(prepared["canvas_size"]),
            province_id_grid=province_grid,
            owner_grid=owner_grid,
            faction_ids=faction_ids,
            province_records=prepared["provinces"],
            attribution=prepared.get("attribution", ""),
        )

    def faction_index(self, faction_id: str) -> int:
        return self.faction_ids.index(faction_id)

    def faction_id(self, owner_index: int) -> str | None:
        if owner_index < 0:
            return None
        return self.faction_ids[int(owner_index)]

    def canvas_to_grid(self, x: float, y: float) -> tuple[int, int]:
        cell_w, cell_h = self.cell_size
        return int(x / cell_w), int(y / cell_h)

    def grid_to_canvas(self, gx: int, gy: int) -> tuple[float, float]:
        cell_w, cell_h = self.cell_size
        return (gx + 0.5) * cell_w, (gy + 0.5) * cell_h

    def is_land_at_canvas(self, x: float, y: float) -> bool:
        gx, gy = self.canvas_to_grid(x, y)
        if gy < 0 or gx < 0 or gy >= self.province_id_grid.shape[0] or gx >= self.province_id_grid.shape[1]:
            return False
        return bool(self.land_mask[gy, gx])

    def random_owned_cell(self, faction_id: str, rng: random.Random) -> tuple[int, int] | None:
        owner_index = self.faction_index(faction_id)
        ys, xs = np.where(self.owner_grid == owner_index)
        if len(xs) == 0:
            return None
        pick = rng.randrange(len(xs))
        return int(xs[pick]), int(ys[pick])

    def owned_cell_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {faction_id: 0 for faction_id in self.faction_ids}
        values, value_counts = np.unique(self.owner_grid[self.owner_grid >= 0], return_counts=True)
        for owner_index, count in zip(values, value_counts, strict=True):
            counts[self.faction_ids[int(owner_index)]] = int(count)
        return counts


@dataclass
class MarbleGameState:
    scenario: MarbleScenarioConfig
    grid: CellGrid
    factions: dict[str, Faction]
    frame: int = 0
    marbles: list[MarbleUnit] = field(default_factory=list)
    resources: dict[str, float] = field(default_factory=dict)
    active_modifiers: list[MarbleModifier] = field(default_factory=list)
    alliances: list[Alliance] = field(default_factory=list)
    triggered_events: list[TriggeredEvent] = field(default_factory=list)
    active_event: TriggeredEvent | None = None

    @property
    def seconds(self) -> float:
        return self.frame / self.scenario.physics.fps

    @property
    def total_frames(self) -> int:
        return int(self.scenario.video_length_seconds * self.scenario.physics.fps)

    @property
    def current_year(self) -> int:
        progress = min(1.0, max(0.0, self.frame / max(1, self.total_frames)))
        return round(self.scenario.start_year + (self.scenario.end_year - self.scenario.start_year) * progress)

    def stat_multiplier(self, faction_id: str, stat: str) -> float:
        multiplier = 1.0
        for modifier in self.active_modifiers:
            if modifier.target == faction_id and modifier.stat == stat:
                multiplier *= modifier.multiplier
        return multiplier

    def are_allied(self, faction_a: str, faction_b: str) -> bool:
        return any(alliance.includes_pair(faction_a, faction_b) for alliance in self.alliances)

