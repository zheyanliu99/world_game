from __future__ import annotations

import math
import random

from hwsim.core.game_state import (
    clamp_faction,
    refresh_eliminations,
    territories_for,
    territory_count,
)
from hwsim.core.models import BattleRecord, Faction, GameState, Region
from hwsim.core.story_director import StoryInfluence
from hwsim.utils.random_utils import clamp, weighted_choice


TERRAIN_DEFENSE = {
    "plain": 1.0,
    "river": 1.13,
    "mountain": 1.22,
    "pass": 1.18,
}


def are_allied(state: GameState, faction_a: str, faction_b: str) -> bool:
    return any(alliance.includes_pair(faction_a, faction_b) for alliance in state.alliances)


def current_multiplier(state: GameState, influence: StoryInfluence, faction_id: str, stat: str) -> float:
    multiplier = influence.stat_multiplier(faction_id, stat)
    for modifier in state.active_modifiers:
        if modifier.target == faction_id and modifier.stat == stat:
            multiplier *= modifier.multiplier
    return multiplier


def collect_border_targets(
    state: GameState,
    attacker_id: str,
    influence: StoryInfluence,
) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for source_region in territories_for(state, attacker_id):
        for neighbor in state.regions[source_region].neighbors:
            defender_id = state.region_owners[neighbor]
            if defender_id == attacker_id:
                continue
            if are_allied(state, attacker_id, defender_id):
                continue
            if defender_id in influence.protected_factions and territory_count(state, defender_id) <= 1:
                continue
            if not state.factions[defender_id].alive:
                continue
            targets.append((source_region, neighbor))
    return targets


def run_battle_tick(
    state: GameState,
    rng: random.Random,
    influence: StoryInfluence,
) -> BattleRecord | None:
    attacker_ids: list[str] = []
    attacker_weights: list[float] = []
    target_cache: dict[str, list[tuple[str, str]]] = {}

    for faction_id, faction in state.factions.items():
        if not faction.alive:
            continue
        targets = collect_border_targets(state, faction_id, influence)
        if not targets:
            continue
        target_cache[faction_id] = targets
        expansion = faction.expansion * current_multiplier(state, influence, faction_id, "expansion")
        weight = max(0.05, expansion) * max(1, territory_count(state, faction_id))
        attacker_ids.append(faction_id)
        attacker_weights.append(weight)

    if not attacker_ids:
        return None

    attacker_id = weighted_choice(rng, attacker_ids, attacker_weights)
    source_region, target_region = _choose_target(state, rng, target_cache[attacker_id])
    defender_id = state.region_owners[target_region]

    attacker = state.factions[attacker_id]
    defender = state.factions[defender_id]
    source = state.regions[source_region]
    target = state.regions[target_region]

    attacker_score = _attacker_score(state, rng, influence, attacker, defender, source, target)
    defender_score = _defender_score(state, rng, influence, defender, target)

    if attacker_score > defender_score:
        state.region_owners[target_region] = attacker_id
        _apply_battle_losses(rng, attacker, defender, attacker_won=True)
        winner = attacker_id
    else:
        _apply_battle_losses(rng, attacker, defender, attacker_won=False)
        winner = defender_id

    clamp_faction(attacker)
    clamp_faction(defender)
    refresh_eliminations(state, influence.protected_factions)

    record = BattleRecord(
        year=state.current_year,
        attacker=attacker_id,
        defender=defender_id,
        source_region=source_region,
        target_region=target_region,
        winner=winner,
        attacker_score=round(attacker_score, 4),
        defender_score=round(defender_score, 4),
    )
    state.battle_log.append(record)
    return record


def _choose_target(
    state: GameState,
    rng: random.Random,
    targets: list[tuple[str, str]],
) -> tuple[str, str]:
    weights: list[float] = []
    for source_region, target_region in targets:
        target = state.regions[target_region]
        source = state.regions[source_region]
        owner = state.region_owners[target_region]
        faction = state.factions[owner]
        value = target.population + target.economy
        border_pressure = 1.1 if target.terrain == "plain" else 0.9
        capital_bonus = 1.25 if faction.capital_region == target_region else 1.0
        southward = 1.04 if target.center[1] > source.center[1] else 1.0
        weights.append(value * border_pressure * capital_bonus * southward)
    return weighted_choice(rng, targets, weights)


def _attacker_score(
    state: GameState,
    rng: random.Random,
    influence: StoryInfluence,
    attacker: Faction,
    defender: Faction,
    source: Region,
    target: Region,
) -> float:
    troop_ratio = math.sqrt(max(1, attacker.troops) / max(1, defender.troops))
    troop_factor = clamp(troop_ratio, 0.62, 1.65)
    morale_factor = clamp(attacker.morale / 68, 0.68, 1.35)
    economy_factor = clamp(0.78 + attacker.economy / 240, 0.75, 1.28)
    naval_factor = 1.0
    if target.terrain == "river" or source.terrain == "river":
        naval_factor = clamp(0.78 + attacker.naval / 3.2, 0.75, 1.25)
    south_attack = 1.0
    if target.center[1] > source.center[1] + 70:
        south_attack = current_multiplier(state, influence, attacker.id, "south_attack")
    return (
        rng.uniform(0.82, 1.2)
        * attacker.attack
        * current_multiplier(state, influence, attacker.id, "attack")
        * attacker.expansion
        * current_multiplier(state, influence, attacker.id, "expansion")
        * troop_factor
        * morale_factor
        * economy_factor
        * naval_factor
        * south_attack
    )


def _defender_score(
    state: GameState,
    rng: random.Random,
    influence: StoryInfluence,
    defender: Faction,
    target: Region,
) -> float:
    morale_factor = clamp(defender.morale / 68, 0.7, 1.35)
    stability_factor = clamp(defender.stability / 70, 0.7, 1.28)
    terrain_factor = TERRAIN_DEFENSE.get(target.terrain, 1.0)
    return (
        rng.uniform(0.84, 1.22)
        * defender.defense
        * current_multiplier(state, influence, defender.id, "defense")
        * terrain_factor
        * morale_factor
        * stability_factor
    )


def _apply_battle_losses(
    rng: random.Random,
    attacker: Faction,
    defender: Faction,
    attacker_won: bool,
) -> None:
    base_loss = rng.randint(900, 3300)
    recruitment = 1.0
    if attacker_won:
        attacker.troops -= int(base_loss * rng.uniform(0.25, 0.48))
        defender.troops -= int(base_loss * rng.uniform(0.72, 1.12))
        attacker.morale += rng.uniform(1.0, 3.2)
        defender.morale -= rng.uniform(2.0, 5.0)
    else:
        attacker.troops -= int(base_loss * rng.uniform(0.72, 1.18))
        defender.troops -= int(base_loss * rng.uniform(0.2, 0.45) * recruitment)
        attacker.morale -= rng.uniform(1.6, 4.0)
        defender.morale += rng.uniform(0.8, 2.8)

