from __future__ import annotations

import random

from hwsim.core.battle import current_multiplier, run_battle_tick
from hwsim.core.event_engine import EventEngine
from hwsim.core.game_state import (
    build_initial_state,
    clamp_faction,
    refresh_eliminations,
    territories_for,
)
from hwsim.core.models import EventConfig, GameState, MapConfig, ScenarioConfig, SimulationResult
from hwsim.core.story_director import StoryDirector
from hwsim.narration.script_generator import build_subtitles, schedule_events


class Simulator:
    def __init__(self, scenario: ScenarioConfig, map_config: MapConfig, event_config: EventConfig) -> None:
        self.scenario = scenario
        self.map_config = map_config
        self.event_engine = EventEngine(event_config)
        self.story_director = StoryDirector(scenario)
        self.rng = random.Random(scenario.random_seed)

    def run(self) -> SimulationResult:
        state = build_initial_state(self.scenario, self.map_config)
        timeline: list[GameState] = []

        for year in range(self.scenario.start_year, self.scenario.end_year + 1):
            state.current_year = year
            influence = self.story_director.influence_for(state)
            self.event_engine.trigger_year_start_events(state)

            for _ in range(self.scenario.ticks_per_year):
                influence = self.story_director.influence_for(state)
                run_battle_tick(state, self.rng, influence)

            self._recruit_for_year(state)
            influence = self.story_director.influence_for(state)
            refresh_eliminations(state, influence.protected_factions)
            timeline.append(state.model_copy(deep=True))
            self.event_engine.expire_year_end_effects(state)

        scheduled_events = schedule_events(self.scenario, state.triggered_events)
        subtitles = build_subtitles(self.scenario, scheduled_events)
        final_state = timeline[-1].model_copy(deep=True)
        return SimulationResult(
            scenario=self.scenario,
            timeline=timeline,
            triggered_events=scheduled_events,
            subtitles=subtitles,
            final_state=final_state,
        )

    def _recruit_for_year(self, state: GameState) -> None:
        influence = self.story_director.influence_for(state)
        for faction_id, faction in state.factions.items():
            if not faction.alive:
                continue
            territory_income = sum(state.regions[region_id].economy for region_id in territories_for(state, faction_id))
            recruitment_multiplier = current_multiplier(state, influence, faction_id, "recruitment")
            yearly_recruits = int((faction.economy * 18 + territory_income * 24) * recruitment_multiplier)
            faction.troops += max(400, yearly_recruits)
            faction.economy += min(1.8, territory_income / 420)
            faction.stability += 0.25 if territory_income else -0.6
            clamp_faction(faction)

