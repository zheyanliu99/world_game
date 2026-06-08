from __future__ import annotations

from hwsim.core.models import Alliance, EventConfig, HistoricalEvent, TriggeredEvent
from hwsim.physics.models import MarbleGameState, MarbleModifier


class MarbleEventDirector:
    def __init__(self, event_config: EventConfig) -> None:
        self.events = sorted(event_config.events, key=lambda event: event.year)
        self.triggered_ids: set[str] = set()

    def update(self, state: MarbleGameState) -> list[TriggeredEvent]:
        self._expire(state)
        triggered: list[TriggeredEvent] = []
        for event in self.events:
            if event.id in self.triggered_ids or state.current_year < event.year:
                continue
            triggered_event = self._trigger(state, event)
            triggered.append(triggered_event)
        active = [
            event
            for event in state.triggered_events
            if event.start_seconds <= state.seconds < event.end_seconds
        ]
        state.active_event = max(active, key=lambda event: event.importance) if active else None
        return triggered

    def _trigger(self, state: MarbleGameState, event: HistoricalEvent) -> TriggeredEvent:
        triggered_event = TriggeredEvent(
            event_id=event.id,
            year=event.year,
            name_cn=event.name_cn,
            title=event.ui.title,
            subtitle=event.ui.subtitle,
            narration=event.narration,
            effect=event.ui.effect,
            focus_regions=event.ui.focus_regions,
            duration_seconds=event.ui.duration_seconds,
            importance=event.importance,
            start_seconds=state.seconds,
            end_seconds=state.seconds + event.ui.duration_seconds,
        )
        for effect in event.effects:
            if effect.type == "marble_modifier":
                if not effect.target or not effect.stat or effect.multiplier is None:
                    raise ValueError(f"Invalid marble_modifier effect in {event.id}")
                state.active_modifiers.append(
                    MarbleModifier(
                        target=effect.target,
                        stat=effect.stat,
                        multiplier=effect.multiplier,
                        expires_frame=state.frame + self._years_to_frames(state, effect.duration_years),
                        source_event=event.id,
                    )
                )
            elif effect.type == "add_resources":
                if effect.target:
                    state.resources[effect.target] = state.resources.get(effect.target, 0) + float(effect.value or 0)
            elif effect.type == "create_alliance":
                if len(effect.factions) == 2:
                    state.alliances.append(
                        Alliance(
                            factions=tuple(sorted(effect.factions)),
                            expires_year=state.current_year + effect.duration_years,
                            source_event=event.id,
                        )
                    )
            elif effect.type == "rename_faction":
                if effect.target and effect.name_cn:
                    state.factions[effect.target].current_name_cn = effect.name_cn
            else:
                # Marble mode deliberately ignores old hard-control effects such as transfer_region.
                continue
        state.triggered_events.append(triggered_event)
        self.triggered_ids.add(event.id)
        return triggered_event

    def _years_to_frames(self, state: MarbleGameState, years: int) -> int:
        year_span = max(1, state.scenario.end_year - state.scenario.start_year)
        frames_per_year = state.total_frames / year_span
        return max(1, int(years * frames_per_year))

    def _expire(self, state: MarbleGameState) -> None:
        state.active_modifiers = [
            modifier for modifier in state.active_modifiers if modifier.expires_frame > state.frame
        ]
        state.alliances = [
            alliance for alliance in state.alliances if alliance.expires_year > state.current_year
        ]
