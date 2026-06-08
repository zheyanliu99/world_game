from __future__ import annotations

from hwsim.core.models import Alliance, Effect, EventConfig, HistoricalEvent, TriggeredEvent
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
            elif effect.type == "add_balls":
                if effect.target:
                    state.pending_ball_adds.append((effect.target, max(0, int(effect.value or 0))))
            elif effect.type == "betrayal":
                self._apply_betrayal(state, effect)
            elif effect.type == "surrender_faction":
                self._apply_faction_surrender(state, effect)
            elif effect.type == "create_alliance":
                if len(effect.factions) == 2:
                    pair = tuple(sorted(effect.factions))
                    state.alliances = [alliance for alliance in state.alliances if alliance.factions != pair]
                    state.alliances.append(
                        Alliance(
                            factions=pair,
                            expires_year=state.current_year + effect.duration_years,
                            source_event=event.id,
                        )
                    )
            elif effect.type == "break_alliance":
                if len(effect.factions) == 2:
                    pair = tuple(sorted(effect.factions))
                    state.alliances = [alliance for alliance in state.alliances if alliance.factions != pair]
            elif effect.type == "rename_faction":
                if effect.target and effect.name_cn:
                    state.factions[effect.target].current_name_cn = effect.name_cn
            else:
                # Marble mode deliberately ignores old hard-control effects such as transfer_region.
                continue
        state.triggered_events.append(triggered_event)
        self.triggered_ids.add(event.id)
        return triggered_event

    def _apply_betrayal(self, state: MarbleGameState, effect: Effect) -> None:
        if not effect.target or not effect.owner or not effect.region:
            return
        if effect.target not in state.grid.faction_ids or effect.owner not in state.grid.faction_ids:
            return
        center = self._province_centroid(state, effect.region)
        if center is None:
            return

        from_index = state.grid.faction_index(effect.target)
        to_index = state.grid.faction_index(effect.owner)
        radius = float(effect.radius or effect.value or 80)
        radius_sq = radius * radius
        cell_w, cell_h = state.grid.cell_size
        center_x, center_y = center

        for gy in range(state.grid.owner_grid.shape[0]):
            y = (gy + 0.5) * cell_h
            for gx in range(state.grid.owner_grid.shape[1]):
                if int(state.grid.owner_grid[gy, gx]) != from_index:
                    continue
                if state.grid.province_id_grid[gy, gx] < 0:
                    continue
                x = (gx + 0.5) * cell_w
                if (x - center_x) ** 2 + (y - center_y) ** 2 <= radius_sq:
                    state.grid.owner_grid[gy, gx] = to_index

        fraction = effect.ball_fraction
        if fraction is None:
            fraction = effect.multiplier if effect.multiplier is not None else 0.35
        fraction = min(1.0, max(0.0, float(fraction)))
        candidates = [
            marble
            for marble in state.marbles
            if marble.faction_id == effect.target and (marble.x - center_x) ** 2 + (marble.y - center_y) ** 2 <= radius_sq
        ]
        candidates.sort(key=lambda marble: (marble.x - center_x) ** 2 + (marble.y - center_y) ** 2)
        convert_count = int(round(len(candidates) * fraction))
        if candidates and fraction > 0:
            convert_count = max(1, convert_count)
        for marble in candidates[:convert_count]:
            marble.faction_id = effect.owner
            marble.cooldown_frames = max(marble.cooldown_frames, 24)

    def _apply_faction_surrender(self, state: MarbleGameState, effect: Effect) -> None:
        if not effect.target or not effect.owner:
            return
        if effect.target not in state.grid.faction_ids or effect.owner not in state.grid.faction_ids:
            return
        from_index = state.grid.faction_index(effect.target)
        to_index = state.grid.faction_index(effect.owner)
        fraction = effect.value if effect.value is not None else 1.0
        fraction = min(1.0, max(0.0, float(fraction)))
        ys, xs = self._owned_cells(state, from_index)
        if len(xs) == 0:
            return
        if fraction >= 1.0:
            state.grid.owner_grid[state.grid.owner_grid == from_index] = to_index
        else:
            convert_count = max(1, int(round(len(xs) * fraction)))
            for y, x in zip(ys[:convert_count], xs[:convert_count], strict=False):
                state.grid.owner_grid[int(y), int(x)] = to_index
        for marble in state.marbles:
            if marble.faction_id == effect.target:
                marble.faction_id = effect.owner
                marble.cooldown_frames = max(marble.cooldown_frames, 45)
        transfer = state.resources.get(effect.target, 0.0) * fraction
        state.resources[effect.target] = max(0.0, state.resources.get(effect.target, 0.0) - transfer)
        state.resources[effect.owner] = state.resources.get(effect.owner, 0.0) + transfer

    def _owned_cells(self, state: MarbleGameState, owner_index: int) -> tuple[list[int], list[int]]:
        ys = []
        xs = []
        height, width = state.grid.owner_grid.shape
        for y in range(height):
            for x in range(width):
                if int(state.grid.owner_grid[y, x]) == owner_index:
                    ys.append(y)
                    xs.append(x)
        return ys, xs

    def _province_centroid(self, state: MarbleGameState, name_fragment: str) -> tuple[float, float] | None:
        needle = name_fragment.lower()
        matches = [
            province
            for province in state.grid.province_records
            if needle in str(province.get("name", "")).lower()
        ]
        if not matches:
            return None
        province = max(matches, key=lambda item: int(item.get("cell_count", 0)))
        centroid = province.get("centroid")
        if not centroid or len(centroid) < 2:
            return None
        return float(centroid[0]), float(centroid[1])

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
