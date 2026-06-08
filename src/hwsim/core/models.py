from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Faction(BaseModel):
    id: str = ""
    name_cn: str
    display_name_by_year: dict[int, str] = Field(default_factory=dict)
    color: str
    troops: int
    morale: float
    economy: float
    stability: float
    legitimacy: float
    attack: float
    defense: float
    naval: float
    expansion: float
    population: int | None = None
    population_spawn_weight: float | None = None
    alive: bool = True
    capital_region: str | None = None
    current_name_cn: str | None = None

    def display_name(self, year: int) -> str:
        if self.current_name_cn:
            return self.current_name_cn
        name = self.name_cn
        for display_year, display_name in sorted(self.display_name_by_year.items()):
            if year >= display_year:
                name = display_name
        return name


class Region(BaseModel):
    id: str
    name_cn: str
    center: tuple[int, int]
    polygon: list[tuple[int, int]]
    terrain: str
    population: int
    economy: int
    initial_owner: str
    neighbors: list[str]


class MapConfig(BaseModel):
    canvas_size: tuple[int, int]
    regions: list[Region]


class Condition(BaseModel):
    type: str
    faction: str | None = None
    value: float | None = None
    region: str | None = None
    owner: str | None = None
    regions: list[str] = Field(default_factory=list)


class Effect(BaseModel):
    type: str
    target: str | None = None
    value: float | int | None = None
    stat: str | None = None
    multiplier: float | None = None
    radius: float | None = None
    ball_fraction: float | None = None
    duration_years: int = 0
    factions: list[str] = Field(default_factory=list)
    region: str | None = None
    owner: str | None = None
    name_cn: str | None = None


class EventUI(BaseModel):
    title: str
    subtitle: str
    focus_regions: list[str] = Field(default_factory=list)
    effect: str = "default_banner"
    duration_seconds: float = 4.0


class HistoricalEvent(BaseModel):
    id: str
    year: int
    name_cn: str
    category: str
    importance: int = 5
    trigger_conditions: list[Condition] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    ui: EventUI
    narration: str


class EventConfig(BaseModel):
    events: list[HistoricalEvent]


class StoryBias(BaseModel):
    id: str
    start_year: int
    end_year: int
    target: str
    modifiers: dict[str, float] = Field(default_factory=dict)
    protect_if_regions_lte: int | None = None

    def is_active(self, year: int) -> bool:
        return self.start_year <= year <= self.end_year


class ScenarioConfig(BaseModel):
    scenario_id: str
    title: str
    output_basename: str = "sanguo_demo_001"
    start_year: int
    end_year: int
    video_length_seconds: int
    render_fps: int = 6
    output_fps: int = 30
    ticks_per_year: int = 4
    target_winner: str
    historical_deviation: float
    random_seed: int
    map_file: Path
    event_file: Path
    style_file: Path
    intro_narration: str
    factions: dict[str, Faction]
    story_biases: list[StoryBias] = Field(default_factory=list)


class StyleConfig(BaseModel):
    canvas_size: tuple[int, int] = (1920, 1080)
    background: str = "#251F19"
    paper: str = "#3A3025"
    border: str = "#D4C6A1"
    text: str = "#F6EAC9"
    muted_text: str = "#BFAF8C"
    subtitle_background: str = "#14110D"
    subtitle_text: str = "#FFF2C7"
    event_title: str = "#FFE7A0"
    event_subtitle: str = "#F9DFAF"
    font_candidates: list[str] = Field(default_factory=list)


class TemporaryModifier(BaseModel):
    source_event: str
    target: str
    stat: str
    multiplier: float
    expires_year: int


class Alliance(BaseModel):
    factions: tuple[str, str]
    expires_year: int
    source_event: str

    def includes_pair(self, faction_a: str, faction_b: str) -> bool:
        return {faction_a, faction_b} == set(self.factions)


class TriggeredEvent(BaseModel):
    event_id: str
    year: int
    name_cn: str
    title: str
    subtitle: str
    narration: str
    effect: str
    focus_regions: list[str]
    duration_seconds: float
    importance: int
    start_seconds: float = 0.0
    end_seconds: float = 0.0


class BattleRecord(BaseModel):
    year: int
    attacker: str
    defender: str
    source_region: str
    target_region: str
    winner: str
    attacker_score: float
    defender_score: float


class GameState(BaseModel):
    current_year: int
    factions: dict[str, Faction]
    regions: dict[str, Region]
    region_owners: dict[str, str]
    active_modifiers: list[TemporaryModifier] = Field(default_factory=list)
    alliances: list[Alliance] = Field(default_factory=list)
    triggered_events: list[TriggeredEvent] = Field(default_factory=list)
    battle_log: list[BattleRecord] = Field(default_factory=list)


class SubtitleEntry(BaseModel):
    start_seconds: float
    end_seconds: float
    text: str


class SimulationResult(BaseModel):
    scenario: ScenarioConfig
    timeline: list[GameState]
    triggered_events: list[TriggeredEvent]
    subtitles: list[SubtitleEntry]
    final_state: GameState

    model_config = {"arbitrary_types_allowed": True}


JsonDict = dict[str, Any]
