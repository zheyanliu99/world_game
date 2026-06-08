from __future__ import annotations

from hwsim.core.models import Faction, GameState, MapConfig, Region, ScenarioConfig
from hwsim.utils.random_utils import clamp


def build_initial_state(scenario: ScenarioConfig, map_config: MapConfig) -> GameState:
    factions = {key: faction.model_copy(deep=True) for key, faction in scenario.factions.items()}
    for faction_id, faction in factions.items():
        faction.id = faction_id
    regions = {region.id: region for region in map_config.regions}
    owners = {region.id: region.initial_owner for region in map_config.regions}
    return GameState(
        current_year=scenario.start_year,
        factions=factions,
        regions=regions,
        region_owners=owners,
    )


def territories_for(state: GameState, faction_id: str) -> list[str]:
    return [region_id for region_id, owner in state.region_owners.items() if owner == faction_id]


def territory_count(state: GameState, faction_id: str) -> int:
    return len(territories_for(state, faction_id))


def alive_factions(state: GameState) -> list[Faction]:
    return [faction for faction in state.factions.values() if faction.alive]


def set_region_owner(state: GameState, region_id: str, owner_id: str) -> None:
    if region_id not in state.regions:
        raise KeyError(f"Unknown region: {region_id}")
    if owner_id not in state.factions:
        raise KeyError(f"Unknown faction: {owner_id}")
    state.region_owners[region_id] = owner_id
    state.factions[owner_id].alive = True


def clamp_faction(faction: Faction) -> None:
    faction.troops = max(0, int(faction.troops))
    faction.morale = clamp(faction.morale, 5, 100)
    faction.economy = clamp(faction.economy, 5, 120)
    faction.stability = clamp(faction.stability, 5, 120)
    faction.legitimacy = clamp(faction.legitimacy, 0, 120)


def refresh_eliminations(state: GameState, protected_factions: set[str] | None = None) -> None:
    protected_factions = protected_factions or set()
    for faction_id, faction in state.factions.items():
        if faction_id in protected_factions:
            faction.alive = True
            continue
        if territory_count(state, faction_id) == 0 or faction.troops <= 0:
            faction.alive = False


def faction_ranking(state: GameState) -> list[tuple[str, int]]:
    return sorted(
        ((faction_id, territory_count(state, faction_id)) for faction_id in state.factions),
        key=lambda item: (item[1], state.factions[item[0]].troops),
        reverse=True,
    )
