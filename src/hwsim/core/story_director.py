from __future__ import annotations

from dataclasses import dataclass, field

from hwsim.core.game_state import territory_count
from hwsim.core.models import GameState, ScenarioConfig


@dataclass
class StoryInfluence:
    modifiers: dict[str, dict[str, float]] = field(default_factory=dict)
    protected_factions: set[str] = field(default_factory=set)

    def stat_multiplier(self, faction_id: str, stat: str) -> float:
        return self.modifiers.get(faction_id, {}).get(stat, 1.0)


class StoryDirector:
    def __init__(self, scenario: ScenarioConfig) -> None:
        self.scenario = scenario

    def influence_for(self, state: GameState) -> StoryInfluence:
        influence = StoryInfluence()
        for bias in self.scenario.story_biases:
            if not bias.is_active(state.current_year):
                continue
            faction_mods = influence.modifiers.setdefault(bias.target, {})
            for stat, multiplier in bias.modifiers.items():
                faction_mods[stat] = faction_mods.get(stat, 1.0) * multiplier
            if (
                bias.protect_if_regions_lte is not None
                and territory_count(state, bias.target) <= bias.protect_if_regions_lte
            ):
                influence.protected_factions.add(bias.target)
        return influence

