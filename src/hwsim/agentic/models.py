from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from hwsim.core.models import Faction, Region


Policy = Literal["balanced", "farming", "war", "logistics", "defense", "diplomacy"]
UnitType = Literal["army", "worker", "scout", "caravan"]
UnitAction = Literal["rest", "attack", "defend", "scout", "farm", "transfer"]
ResourceType = Literal["food", "weapons", "gold", "manpower", "intel"]
DiplomacyAction = Literal["propose_alliance", "break_alliance"]


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
    readiness: int = 100
    power: float = 1.0
    cargo: dict[ResourceType, int] = Field(default_factory=dict)
    status: str = "idle"


class AgentOrder(BaseModel):
    unit_id: str | None = None
    action: UnitAction
    region_id: str | None = None
    target_region_id: str | None = None
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
    units: list[AgenticUnit]
    neighbors: dict[str, list[str]]
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


class AgenticGameState(BaseModel):
    game_id: str
    round: int = 0
    max_rounds: int = 20
    player_faction: str = "liu_bei"
    factions: dict[str, Faction]
    regions: dict[str, Region]
    region_owners: dict[str, str]
    units: list[AgenticUnit]
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


class GameView(BaseModel):
    game_id: str
    round: int
    max_rounds: int
    player_faction: str
    finished: bool
    winner: str | None
    regions: list[Region]
    region_owners: dict[str, str]
    region_development: dict[str, float]
    regional_supply: dict[str, dict[ResourceType, int]]
    region_pressure: dict[str, int]
    factions: dict[str, FactionView]
    units: list[AgenticUnit]
    alliances: list[AgenticAlliance]
    logs: list[RoundLog]
    current_player_command: str
    current_player_policy: Policy
    current_player_orders: list[AgentOrder]
