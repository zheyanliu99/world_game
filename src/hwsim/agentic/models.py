from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from hwsim.core.models import Faction, Region


Policy = Literal["balanced", "farming", "war", "logistics", "defense", "diplomacy"]
UnitType = Literal["army", "worker", "scout", "caravan"]
UnitAction = Literal["rest", "attack", "defend", "scout", "farm", "transfer"]
ResourceType = Literal["food", "weapons", "gold", "manpower", "intel"]
DiplomacyAction = Literal["propose_alliance", "break_alliance"]
BattleOutcome = Literal["attacker_win", "defender_win", "contested"]
DefenderAftermath = Literal["hold", "retreat", "surrender_soldiers", "surrender_general", "annihilated"]


POLICIES: tuple[Policy, ...] = ("balanced", "farming", "war", "logistics", "defense", "diplomacy")
UNIT_TYPES: tuple[UnitType, ...] = ("army", "worker", "scout", "caravan")
UNIT_ACTIONS: tuple[UnitAction, ...] = ("rest", "attack", "defend", "scout", "farm", "transfer")
RESOURCE_TYPES: tuple[ResourceType, ...] = ("food", "weapons", "gold", "manpower", "intel")


class Resources(BaseModel):
    food: int = 0
    weapons: int = 0
    gold: int = 0
    manpower: int = 0
    intel: int = 0

    def add(self, resource: ResourceType, amount: int) -> None:
        setattr(self, resource, max(0, int(getattr(self, resource) + amount)))

    def spend(self, resource: ResourceType, amount: int) -> bool:
        amount = max(0, int(amount))
        current = int(getattr(self, resource))
        if current < amount:
            return False
        setattr(self, resource, current - amount)
        return True


class AgenticUnit(BaseModel):
    id: str
    faction_id: str
    unit_type: UnitType
    region_id: str
    city_id: str | None = None
    general_id: str | None = None
    soldiers: int = 0
    max_soldiers: int = 0
    readiness: int = 100
    power: float = 1.0
    cargo: dict[ResourceType, int] = Field(default_factory=dict)
    status: str = "idle"


class AgentOrder(BaseModel):
    unit_id: str | None = None
    general_id: str | None = None
    action: UnitAction
    region_id: str | None = None
    target_region_id: str | None = None
    source_city_id: str | None = None
    target_city_id: str | None = None
    target_city_ids: list[str] = Field(default_factory=list)
    target_faction: str | None = None
    resource: ResourceType | None = None
    amount: int = 0


class DiplomacyOrder(BaseModel):
    type: DiplomacyAction
    target: str
    duration_rounds: int = 4


class AgentPlan(BaseModel):
    policy: Policy = "balanced"
    orders: list[AgentOrder] = Field(default_factory=list)
    diplomacy: list[DiplomacyOrder] = Field(default_factory=list)
    reasoning_summary: str = ""


class AgentObservation(BaseModel):
    faction_id: str
    round: int
    max_rounds: int
    policy: Policy
    resources: Resources
    owned_regions: list[str]
    visible_regions: dict[str, str]
    visible_cities: dict[str, str] = Field(default_factory=dict)
    units: list[AgenticUnit]
    neighbors: dict[str, list[str]]
    city_neighbors: dict[str, list[str]] = Field(default_factory=dict)
    alliances: list[str]
    recent_log: list[str]
    player_command: str = ""


class AgenticAlliance(BaseModel):
    factions: tuple[str, str]
    expires_round: int
    source: str = "diplomacy"

    def includes_pair(self, faction_a: str, faction_b: str) -> bool:
        return {faction_a, faction_b} == set(self.factions)

    def other(self, faction_id: str) -> str | None:
        if faction_id not in self.factions:
            return None
        return self.factions[1] if self.factions[0] == faction_id else self.factions[0]


class RoundLog(BaseModel):
    round: int
    title: str
    detail: str
    faction_id: str | None = None
    region_id: str | None = None
    tone: str = "neutral"


class PendingAttack(BaseModel):
    faction_id: str
    unit_id: str
    source_region_id: str
    target_region_id: str
    attack_score: float
    source_city_id: str | None = None
    target_city_ids: list[str] = Field(default_factory=list)


class CityView(BaseModel):
    id: str
    name_cn: str
    region_id: str
    position: tuple[float, float]
    terrain: str
    population: int
    economy: int
    fort: float
    neighbors: list[str]


class GeneralView(BaseModel):
    id: str
    name_cn: str
    name_en: str
    faction_id: str
    portrait_path: str
    city_id: str | None
    unit_id: str | None = None
    soldiers: int
    max_soldiers: int
    command: int
    attack: int
    defense: int
    mobility: int
    loyalty: int
    food_need: int
    surrender_risk: float
    status: str = "idle"


class BattleEvent(BaseModel):
    round: int
    attacker_faction: str
    defender_faction: str
    attacker_general_id: str
    defender_general_id: str | None = None
    source_city_id: str
    target_city_id: str
    outcome: BattleOutcome
    aftermath: DefenderAftermath
    attacker_before: int
    attacker_after: int
    defender_before: int
    defender_after: int
    win_probability: float
    factors: list[str] = Field(default_factory=list)
    summary: str


class AnimationEvent(BaseModel):
    type: str
    faction_id: str | None = None
    general_id: str | None = None
    from_city_id: str | None = None
    to_city_id: str | None = None
    city_id: str | None = None
    value: int | None = None
    tone: str = "neutral"


class AgenticGameState(BaseModel):
    game_id: str
    round: int = 0
    max_rounds: int = 20
    player_faction: str = "liu_bei"
    factions: dict[str, Faction]
    regions: dict[str, Region]
    region_owners: dict[str, str]
    cities: dict[str, CityView] = Field(default_factory=dict)
    city_owners: dict[str, str] = Field(default_factory=dict)
    city_development: dict[str, float] = Field(default_factory=dict)
    city_supply: dict[str, dict[ResourceType, int]] = Field(default_factory=dict)
    units: list[AgenticUnit]
    generals: dict[str, GeneralView] = Field(default_factory=dict)
    resources: dict[str, Resources]
    policies: dict[str, Policy]
    alliances: list[AgenticAlliance] = Field(default_factory=list)
    region_development: dict[str, float] = Field(default_factory=dict)
    regional_supply: dict[str, dict[ResourceType, int]] = Field(default_factory=dict)
    region_pressure: dict[str, int] = Field(default_factory=dict)
    current_player_command: str = ""
    current_player_policy: Policy = "balanced"
    current_player_orders: list[AgentOrder] = Field(default_factory=list)
    current_player_diplomacy: list[DiplomacyOrder] = Field(default_factory=list)
    last_plans: dict[str, AgentPlan] = Field(default_factory=dict)
    battle_events: list[BattleEvent] = Field(default_factory=list)
    animations: list[AnimationEvent] = Field(default_factory=list)
    logs: list[RoundLog] = Field(default_factory=list)
    winner: str | None = None
    finished: bool = False


class FactionView(BaseModel):
    id: str
    name: str
    color: str
    policy: Policy
    resources: Resources
    region_count: int
    unit_count: int
    score: int
    ai_intent: str = ""


class RealMapProvinceView(BaseModel):
    id: int
    name: str
    region_id: str
    centroid: tuple[float, float]
    cell_count: int


class RealMapStateLabel(BaseModel):
    id: str
    name_cn: str
    centroid: tuple[float, float]


class RealMapView(BaseModel):
    canvas_size: tuple[int, int]
    grid_size: tuple[int, int]
    province_id_grid: list[list[int]]
    provinces: list[RealMapProvinceView]
    state_labels: list[RealMapStateLabel]
    attribution: str = ""


class GameView(BaseModel):
    game_id: str
    round: int
    max_rounds: int
    player_faction: str
    finished: bool
    winner: str | None
    real_map: RealMapView | None = None
    regions: list[Region]
    region_owners: dict[str, str]
    cities: list[CityView] = Field(default_factory=list)
    city_owners: dict[str, str] = Field(default_factory=dict)
    city_development: dict[str, float] = Field(default_factory=dict)
    city_supply: dict[str, dict[ResourceType, int]] = Field(default_factory=dict)
    region_development: dict[str, float]
    regional_supply: dict[str, dict[ResourceType, int]]
    region_pressure: dict[str, int]
    factions: dict[str, FactionView]
    units: list[AgenticUnit]
    generals: list[GeneralView] = Field(default_factory=list)
    battle_events: list[BattleEvent] = Field(default_factory=list)
    animations: list[AnimationEvent] = Field(default_factory=list)
    alliances: list[AgenticAlliance]
    logs: list[RoundLog]
    current_player_command: str
    current_player_policy: Policy
    current_player_orders: list[AgentOrder]
