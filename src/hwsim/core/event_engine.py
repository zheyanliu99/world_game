from __future__ import annotations

from hwsim.core.game_state import clamp_faction, set_region_owner
from hwsim.core.models import (
    Alliance,
    Condition,
    Effect,
    EventConfig,
    GameState,
    HistoricalEvent,
    TemporaryModifier,
    TriggeredEvent,
)


class EventEngine:
    def __init__(self, event_config: EventConfig) -> None:
        self.events_by_year: dict[int, list[HistoricalEvent]] = {}
        for event in event_config.events:
            self.events_by_year.setdefault(event.year, []).append(event)
        self.triggered_ids: set[str] = set()

    def trigger_year_start_events(self, state: GameState) -> list[TriggeredEvent]:
        triggered: list[TriggeredEvent] = []
        for event in self.events_by_year.get(state.current_year, []):
            if event.id in self.triggered_ids:
                continue
            if not self._conditions_met(state, event.trigger_conditions):
                continue
            self._apply_effects(state, event)
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
            )
            state.triggered_events.append(triggered_event)
            self.triggered_ids.add(event.id)
            triggered.append(triggered_event)
        return triggered

    def expire_year_end_effects(self, state: GameState) -> None:
        state.active_modifiers = [
            modifier for modifier in state.active_modifiers if modifier.expires_year > state.current_year
        ]
        state.alliances = [alliance for alliance in state.alliances if alliance.expires_year > state.current_year]

    def _conditions_met(self, state: GameState, conditions: list[Condition]) -> bool:
        return all(self._condition_met(state, condition) for condition in conditions)

    def _condition_met(self, state: GameState, condition: Condition) -> bool:
        if condition.type == "faction_alive":
            return bool(condition.faction and state.factions[condition.faction].alive)
        if condition.type == "faction_troops_gte":
            return bool(condition.faction and state.factions[condition.faction].troops >= (condition.value or 0))
        if condition.type == "faction_controls_any":
            return bool(
                condition.faction
                and any(state.region_owners.get(region_id) == condition.faction for region_id in condition.regions)
            )
        if condition.type == "faction_controls_gte":
            if not condition.faction:
                return False
            count = sum(1 for owner in state.region_owners.values() if owner == condition.faction)
            return count >= int(condition.value or 0)
        if condition.type == "region_owner":
            return bool(condition.region and state.region_owners.get(condition.region) == condition.owner)
        raise ValueError(f"Unsupported event condition: {condition.type}")

    def _apply_effects(self, state: GameState, event: HistoricalEvent) -> None:
        for effect in event.effects:
            self._apply_effect(state, event, effect)
        for faction in state.factions.values():
            clamp_faction(faction)

    def _apply_effect(self, state: GameState, event: HistoricalEvent, effect: Effect) -> None:
        faction = state.factions.get(effect.target or "") if effect.target else None

        if effect.type == "add_morale" and faction:
            faction.morale += float(effect.value or 0)
        elif effect.type == "add_stability" and faction:
            faction.stability += float(effect.value or 0)
        elif effect.type == "add_legitimacy" and faction:
            faction.legitimacy += float(effect.value or 0)
        elif effect.type == "add_economy" and faction:
            faction.economy += float(effect.value or 0)
        elif effect.type == "add_troops" and faction:
            faction.troops += int(effect.value or 0)
        elif effect.type == "multiply_troops" and faction:
            faction.troops = int(faction.troops * float(effect.value or 1))
        elif effect.type == "rename_faction" and faction:
            faction.current_name_cn = effect.name_cn or faction.current_name_cn
        elif effect.type == "temporary_modifier":
            if not effect.target or not effect.stat or effect.multiplier is None:
                raise ValueError(f"Invalid temporary modifier effect in {event.id}")
            state.active_modifiers.append(
                TemporaryModifier(
                    source_event=event.id,
                    target=effect.target,
                    stat=effect.stat,
                    multiplier=effect.multiplier,
                    expires_year=state.current_year + effect.duration_years,
                )
            )
        elif effect.type == "create_alliance":
            if len(effect.factions) != 2:
                raise ValueError(f"Only two-faction alliances are supported in V1: {event.id}")
            state.alliances.append(
                Alliance(
                    factions=tuple(sorted(effect.factions)),
                    expires_year=state.current_year + effect.duration_years,
                    source_event=event.id,
                )
            )
        elif effect.type == "transfer_region":
            if not effect.region or not effect.owner:
                raise ValueError(f"Invalid transfer_region effect in {event.id}")
            set_region_owner(state, effect.region, effect.owner)
        else:
            raise ValueError(f"Unsupported event effect: {effect.type}")

