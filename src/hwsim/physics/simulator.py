from __future__ import annotations

import math
import random
from collections import defaultdict, deque
from pathlib import Path

import numpy as np

from hwsim.core.models import EventConfig, TriggeredEvent
from hwsim.map.real_map import load_real_map_config, prepare_real_map
from hwsim.physics.director import MarbleEventDirector
from hwsim.physics.models import CellGrid, MarbleGameState, MarbleScenarioConfig, MarbleUnit
from hwsim.utils.file_utils import read_json, resolve_path


def load_marble_scenario(path: str | Path) -> MarbleScenarioConfig:
    scenario = MarbleScenarioConfig.model_validate(read_json(resolve_path(path)))
    for faction_id, faction in scenario.factions.items():
        faction.id = faction_id
    return scenario


def load_marble_game_inputs(
    scenario_path: str | Path,
    prepare_map: bool = True,
) -> tuple[MarbleScenarioConfig, dict, EventConfig]:
    scenario = load_marble_scenario(scenario_path)
    real_map_config = load_real_map_config(scenario.real_map_file)
    prepared_path = prepare_real_map(scenario.real_map_file) if prepare_map else resolve_path(real_map_config.prepared_map_file)
    prepared_map = read_json(prepared_path)
    if scenario.state_file:
        prepared_map = dict(prepared_map)
        prepared_map["state_regions"] = read_json(resolve_path(scenario.state_file)).get("states", [])
    event_config = EventConfig.model_validate(read_json(resolve_path(scenario.event_file)))
    return scenario, prepared_map, event_config


class MarbleSimulator:
    def __init__(self, scenario: MarbleScenarioConfig, prepared_map: dict, event_config: EventConfig) -> None:
        self.scenario = scenario
        self.rng = random.Random(scenario.random_seed)
        self.director = MarbleEventDirector(event_config)
        self.next_marble_id = 1
        self.state = self._build_initial_state(prepared_map)

    def step(self, frames: int = 1) -> MarbleGameState:
        for _ in range(frames):
            self.director.update(self.state)
            self._consume_pending_ball_adds()
            self._spawn_resources()
            for _substep in range(max(1, self.scenario.physics.substeps)):
                self._move_marbles()
                self._resolve_collisions()
            if self._is_population_tick():
                self._apply_collapse_pressure()
                self._apply_land_share_surrenders()
                self._update_state_control_transfers()
                self._cleanup_small_enclaves()
            for marble in self.state.marbles:
                marble.age_frames += 1
                marble.cooldown_frames = max(0, marble.cooldown_frames - 1)
            self.state.frame += 1
        return self.state

    def run_until_end(self) -> MarbleGameState:
        while self.state.frame < self.state.total_frames:
            self.step()
        return self.state

    def _build_initial_state(self, prepared_map: dict) -> MarbleGameState:
        faction_ids = list(self.scenario.factions.keys())
        grid = CellGrid.from_prepared_map(prepared_map, faction_ids)
        state = MarbleGameState(
            scenario=self.scenario,
            grid=grid,
            factions={key: value.model_copy(deep=True) for key, value in self.scenario.factions.items()},
            resources={faction_id: 100.0 for faction_id in faction_ids},
        )
        state.state_controllers = self._current_state_controllers(state)
        for faction_id in faction_ids:
            for _ in range(self._initial_marble_count(faction_id)):
                self._spawn_marble(state, faction_id, free=True)
        return state

    def _spawn_resources(self) -> None:
        if not self._is_population_tick():
            return
        counts = self.state.grid.owned_cell_counts()
        growth_multiplier = self._population_growth_multiplier()
        for faction_id, count in counts.items():
            spawn_rate = self.state.stat_multiplier(faction_id, "spawn_rate")
            population_income = 0.55 + self._population_weight(faction_id) * 0.65
            income = (
                count
                / 100
                * self.scenario.physics.resource_gain_per_100_cells
                * spawn_rate
                * population_income
                * growth_multiplier
            )
            self.state.resources[faction_id] = self.state.resources.get(faction_id, 0) + income
        self._add_state_control_population_income(growth_multiplier)
        for faction_id in counts:
            while (
                self.state.resources[faction_id] >= self.scenario.physics.spawn_cost
                and self._marble_count(faction_id) < self._max_marble_count(faction_id)
            ):
                if self._spawn_marble(self.state, faction_id, free=False):
                    self.state.resources[faction_id] -= self.scenario.physics.spawn_cost
                else:
                    break

    def _add_state_control_population_income(self, growth_multiplier: float) -> None:
        state_grid = self.state.grid.state_id_grid
        if state_grid is None or not self.state.grid.state_records:
            return
        threshold = min(1.0, max(0.1, self.scenario.physics.state_control_threshold))
        gain = self.scenario.physics.state_control_population_gain_per_100_cells
        if gain <= 0:
            return
        for state_index, state_record in enumerate(self.state.grid.state_records):
            mask = state_grid == state_index
            total = int(mask.sum())
            if total <= 0:
                continue
            owner_values, owner_counts = np_unique_nonnegative(self.state.grid.owner_grid[mask])
            if not owner_values:
                continue
            top_position = max(range(len(owner_counts)), key=owner_counts.__getitem__)
            top_count = owner_counts[top_position]
            ratio = top_count / total
            if ratio < threshold:
                continue
            faction_id = self.state.grid.faction_id(owner_values[top_position])
            if not faction_id:
                continue
            state_weight = float(state_record.get("population_weight", 1.0))
            income = total / 100 * gain * state_weight * ratio * growth_multiplier
            self.state.resources[faction_id] = self.state.resources.get(faction_id, 0) + income

    def _consume_pending_ball_adds(self) -> None:
        if not self.state.pending_ball_adds:
            return
        pending = self.state.pending_ball_adds
        self.state.pending_ball_adds = []
        for faction_id, amount, center in pending:
            for _ in range(amount):
                if self._marble_count(faction_id) >= self._max_marble_count(faction_id):
                    break
                if not self._spawn_marble(self.state, faction_id, free=True, center=center):
                    break

    def _spawn_marble(
        self,
        state: MarbleGameState,
        faction_id: str,
        free: bool,
        center: tuple[float, float] | None = None,
    ) -> bool:
        cell = self._owned_cell_near_center(state, faction_id, center) if center else None
        if cell is None:
            cell = state.grid.random_owned_cell(faction_id, self.rng)
        if cell is None:
            return False
        x, y = state.grid.grid_to_canvas(*cell)
        angle = self.rng.uniform(0, math.tau)
        speed = self.rng.uniform(self.scenario.physics.min_speed, self.scenario.physics.max_speed)
        radius = self.scenario.physics.base_radius * max(0.7, state.stat_multiplier(faction_id, "radius"))
        marble = MarbleUnit(
            id=self.next_marble_id,
            faction_id=faction_id,
            x=x,
            y=y,
            vx=math.cos(angle) * speed,
            vy=math.sin(angle) * speed,
            radius=radius,
            power=max(0.45, state.stat_multiplier(faction_id, "power")),
        )
        self.next_marble_id += 1
        state.marbles.append(marble)
        if not free:
            marble.cooldown_frames = 12
        return True

    def _owned_cell_near_center(
        self,
        state: MarbleGameState,
        faction_id: str,
        center: tuple[float, float] | None,
    ) -> tuple[int, int] | None:
        if center is None:
            return None
        owner_index = state.grid.faction_index(faction_id)
        center_gx, center_gy = state.grid.canvas_to_grid(*center)
        grid_h, grid_w = state.grid.owner_grid.shape
        max_radius = max(grid_w, grid_h)
        candidates: list[tuple[int, int]] = []
        for radius in range(0, max_radius + 1):
            for gy in range(max(0, center_gy - radius), min(grid_h, center_gy + radius + 1)):
                for gx in range(max(0, center_gx - radius), min(grid_w, center_gx + radius + 1)):
                    if abs(gx - center_gx) != radius and abs(gy - center_gy) != radius:
                        continue
                    if int(state.grid.owner_grid[gy, gx]) == owner_index:
                        candidates.append((gx, gy))
            if candidates:
                return candidates[self.rng.randrange(len(candidates))]
        return None

    def _move_marbles(self) -> None:
        dt = 1 / self.scenario.physics.fps / max(1, self.scenario.physics.substeps)
        width, height = self.state.grid.canvas_size
        for marble in self.state.marbles:
            old_x, old_y = marble.x, marble.y
            effective_dt = dt * self.state.stat_multiplier(marble.faction_id, "speed")
            next_x = marble.x + marble.vx * effective_dt
            next_y = marble.y + marble.vy * effective_dt
            hit_x = next_x - marble.radius < 0 or next_x + marble.radius > width
            hit_y = next_y - marble.radius < 0 or next_y + marble.radius > height

            if hit_x or hit_y:
                marble.x = min(max(next_x, marble.radius), width - marble.radius)
                marble.y = min(max(next_y, marble.radius), height - marble.radius)
                self._reflect_axes(marble, reflect_x=hit_x, reflect_y=hit_y, steer=True)
                continue

            blocker = self._movement_blocker(marble, next_x, next_y)
            if blocker == "enemy":
                marble.x, marble.y = old_x, old_y
                reflect_x, reflect_y = self._reflection_axes_for_block(marble, old_x, old_y, effective_dt)
                self._capture_cells_for_marble(marble, center=(next_x, next_y))
                self._reflect_axes(marble, reflect_x=reflect_x, reflect_y=reflect_y, steer=False)
            elif blocker == "land":
                marble.x, marble.y = old_x, old_y
                self._reflect_from_block(marble, old_x, old_y, effective_dt)
            else:
                marble.x, marble.y = next_x, next_y

    def _capture_cells(self) -> None:
        for marble in self.state.marbles:
            self._capture_cells_for_marble(marble)

    def _capture_cells_for_marble(self, marble: MarbleUnit, center: tuple[float, float] | None = None) -> None:
        if not self._can_capture_new_land(marble.faction_id):
            return
        cell_w, cell_h = self.state.grid.cell_size
        radius_cells = max(1, int(math.ceil(self.scenario.physics.capture_radius / min(cell_w, cell_h))))
        owner_index = self.state.grid.faction_index(marble.faction_id)
        center_x, center_y = center if center is not None else (marble.x, marble.y)
        gx, gy = self.state.grid.canvas_to_grid(center_x, center_y)
        capture_mult = self.state.stat_multiplier(marble.faction_id, "capture")
        for dy in range(-radius_cells, radius_cells + 1):
            for dx in range(-radius_cells, radius_cells + 1):
                if dx * dx + dy * dy > radius_cells * radius_cells:
                    continue
                x = gx + dx
                y = gy + dy
                if y < 0 or x < 0 or y >= self.state.grid.owner_grid.shape[0] or x >= self.state.grid.owner_grid.shape[1]:
                    continue
                if self.state.grid.province_id_grid[y, x] < 0:
                    continue
                current_owner = int(self.state.grid.owner_grid[y, x])
                if current_owner == owner_index:
                    continue
                current_faction = self.state.grid.faction_id(current_owner)
                if current_faction and self.state.are_allied(marble.faction_id, current_faction):
                    continue
                defense = self.state.stat_multiplier(current_faction, "defense") if current_faction else 1.0
                chance = min(0.96, 0.72 * capture_mult * marble.power / max(0.2, defense))
                if self.rng.random() < chance:
                    self.state.grid.owner_grid[y, x] = owner_index

    def _is_population_tick(self) -> bool:
        return self.state.frame % max(1, self.scenario.physics.spawn_interval_frames) == 0

    def _can_capture_new_land(self, faction_id: str) -> bool:
        threshold = self.scenario.physics.min_land_share_to_capture
        return threshold <= 0 or self._land_share(faction_id) >= threshold

    def _land_share(self, faction_id: str) -> float:
        total = int((self.state.grid.owner_grid >= 0).sum())
        if total <= 0:
            return 0.0
        owner_index = self.state.grid.faction_index(faction_id)
        return float((self.state.grid.owner_grid == owner_index).sum()) / total

    def _apply_collapse_pressure(self) -> None:
        threshold = self.scenario.physics.min_land_share_to_capture
        if threshold <= 0:
            return
        resource_decay = min(1.0, max(0.0, self.scenario.physics.collapse_resource_decay))
        loss_interval = self.scenario.physics.collapse_unit_loss_interval_frames
        for faction_id in self.state.grid.faction_ids:
            if self._land_share(faction_id) >= threshold:
                continue
            if resource_decay > 0:
                self.state.resources[faction_id] = self.state.resources.get(faction_id, 0.0) * (1.0 - resource_decay)
            if loss_interval <= 0 or self.state.frame % loss_interval != 0:
                continue
            candidates = [marble for marble in self.state.marbles if marble.faction_id == faction_id]
            if not candidates:
                continue
            loss_count = max(1, int(round(len(candidates) * max(0.03, resource_decay))))
            doomed_ids = {
                marble.id
                for marble in sorted(candidates, key=lambda item: (item.power, -item.age_frames))[:loss_count]
            }
            self.state.marbles = [marble for marble in self.state.marbles if marble.id not in doomed_ids]

    def _apply_land_share_surrenders(self) -> None:
        if self.state.last_surrender_check_year == self.state.current_year:
            return
        self.state.last_surrender_check_year = self.state.current_year
        partial_threshold = self.scenario.physics.partial_surrender_land_share
        whole_threshold = self.scenario.physics.whole_surrender_land_share
        if partial_threshold <= 0 and whole_threshold <= 0:
            return
        shares = {faction_id: self._land_share(faction_id) for faction_id in self.state.grid.faction_ids}
        for faction_id, share in sorted(shares.items(), key=lambda item: item[1]):
            if share <= 0:
                continue
            winner = self._surrender_recipient(faction_id)
            if winner is None:
                continue
            if whole_threshold > 0 and share < whole_threshold:
                if self.rng.random() < min(1.0, max(0.0, self.scenario.physics.whole_surrender_chance)):
                    self._surrender_faction(faction_id, winner)
                    continue
            if partial_threshold > 0 and share < partial_threshold:
                if self.rng.random() < min(1.0, max(0.0, self.scenario.physics.partial_surrender_chance)):
                    self._partial_surrender(faction_id, winner)

    def _surrender_recipient(self, loser: str) -> str | None:
        if self.scenario.target_winner and self.scenario.target_winner != loser:
            if self.scenario.target_winner in self.state.grid.faction_ids:
                return self.scenario.target_winner
        counts = self.state.grid.owned_cell_counts()
        candidates = [(faction_id, count) for faction_id, count in counts.items() if faction_id != loser and count > 0]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[1])[0]

    def _partial_surrender(self, loser: str, winner: str) -> None:
        loser_index = self.state.grid.faction_index(loser)
        winner_index = self.state.grid.faction_index(winner)
        owner_grid = self.state.grid.owner_grid
        state_grid = self.state.grid.state_id_grid
        if state_grid is None:
            candidate_mask = owner_grid == loser_index
            state_index = None
        else:
            state_counts: list[tuple[int, int]] = []
            for state_index in range(len(self.state.grid.state_records)):
                count = int(((state_grid == state_index) & (owner_grid == loser_index)).sum())
                if count > 0:
                    state_counts.append((state_index, count))
            if not state_counts:
                return
            state_index = max(state_counts, key=lambda item: item[1])[0]
            candidate_mask = (state_grid == state_index) & (owner_grid == loser_index)
        ys, xs = np.where(candidate_mask)
        if len(xs) == 0:
            return
        fraction = min(1.0, max(0.01, self.scenario.physics.partial_surrender_fraction))
        convert_count = max(1, int(round(len(xs) * fraction)))
        center_x = float(xs.mean())
        center_y = float(ys.mean())
        picks = sorted(
            range(len(xs)),
            key=lambda index: (float(xs[index]) - center_x) ** 2 + (float(ys[index]) - center_y) ** 2,
        )
        converted_points: list[tuple[int, int]] = []
        for pick in picks[:convert_count]:
            gy = int(ys[pick])
            gx = int(xs[pick])
            owner_grid[gy, gx] = winner_index
            converted_points.append((gx, gy))
        self._convert_surrendered_units(loser, winner, state_index, self.scenario.physics.surrender_unit_fraction)
        spawn_center = self._converted_cell_center(converted_points)
        spawn_amount = max(1, min(10, convert_count // 120))
        self.state.pending_ball_adds.append((winner, spawn_amount, spawn_center))
        self._transfer_population_pool(loser, winner, fraction * 0.5)
        self._add_surrender_event("partial", loser, winner)

    def _surrender_faction(self, loser: str, winner: str) -> None:
        loser_index = self.state.grid.faction_index(loser)
        winner_index = self.state.grid.faction_index(winner)
        self.state.grid.owner_grid[self.state.grid.owner_grid == loser_index] = winner_index
        for marble in self.state.marbles:
            if marble.faction_id == loser:
                marble.faction_id = winner
                marble.cooldown_frames = max(marble.cooldown_frames, 45)
        self._transfer_population_pool(loser, winner, 1.0)
        self._add_surrender_event("whole", loser, winner)

    def _convert_surrendered_units(
        self,
        loser: str,
        winner: str,
        state_index: int | None,
        fraction: float,
    ) -> None:
        fraction = min(1.0, max(0.0, fraction))
        if fraction <= 0:
            return
        candidates = [
            marble
            for marble in self.state.marbles
            if marble.faction_id == loser and (state_index is None or self._marble_state_index(marble) == state_index)
        ]
        if not candidates:
            return
        convert_count = max(1, int(round(len(candidates) * fraction)))
        self.rng.shuffle(candidates)
        for marble in candidates[:convert_count]:
            marble.faction_id = winner
            marble.cooldown_frames = max(marble.cooldown_frames, 35)

    def _transfer_population_pool(self, loser: str, winner: str, fraction: float) -> None:
        fraction = min(1.0, max(0.0, fraction))
        transfer = self.state.resources.get(loser, 0.0) * fraction
        self.state.resources[loser] = max(0.0, self.state.resources.get(loser, 0.0) - transfer)
        self.state.resources[winner] = self.state.resources.get(winner, 0.0) + transfer

    def _converted_cell_center(self, points: list[tuple[int, int]]) -> tuple[float, float] | None:
        if not points:
            return None
        avg_x = sum(x for x, _y in points) / len(points)
        avg_y = sum(y for _x, y in points) / len(points)
        return self.state.grid.grid_to_canvas(int(round(avg_x)), int(round(avg_y)))

    def _add_surrender_event(self, kind: str, loser: str, winner: str) -> None:
        loser_name = self.state.factions[loser].display_name(self.state.current_year)
        winner_name = self.state.factions[winner].display_name(self.state.current_year)
        title = "举国归降" if kind == "whole" else "局部归降"
        subtitle = f"{loser_name}人口与土地转向{winner_name}"
        event = TriggeredEvent(
            event_id=f"system_{kind}_surrender_{loser}_{winner}_{self.state.current_year}_{self.state.frame}",
            year=self.state.current_year,
            name_cn=title,
            title=title,
            subtitle=subtitle,
            narration=subtitle,
            effect="betrayal_flash",
            focus_regions=[],
            duration_seconds=3.0,
            importance=9 if kind == "whole" else 7,
            start_seconds=self.state.seconds,
            end_seconds=self.state.seconds + 3.0,
        )
        self.state.triggered_events.append(event)
        self.state.active_event = event

    def _update_state_control_transfers(self) -> None:
        current_controllers = self._current_state_controllers(self.state)
        for state_id, new_owner in current_controllers.items():
            previous_owner = self.state.state_controllers.get(state_id)
            if previous_owner is None:
                self.state.state_controllers[state_id] = new_owner
                continue
            if previous_owner == new_owner:
                continue
            self._transfer_state_population(state_id, previous_owner, new_owner)
            self.state.state_controllers[state_id] = new_owner

    def _cleanup_small_enclaves(self) -> None:
        interval = self.scenario.physics.enclave_cleanup_interval_frames
        max_cells = self.scenario.physics.enclave_cleanup_max_cells
        if interval <= 0 or max_cells <= 0 or self.state.frame % interval != 0:
            return
        owner_grid = self.state.grid.owner_grid
        height, width = owner_grid.shape
        visited = np.zeros(owner_grid.shape, dtype=bool)
        conversions: list[tuple[list[tuple[int, int]], int]] = []

        for start_y in range(height):
            for start_x in range(width):
                owner = int(owner_grid[start_y, start_x])
                if owner < 0 or visited[start_y, start_x]:
                    continue
                component: list[tuple[int, int]] = []
                neighbor_counts: dict[int, int] = {}
                queue: deque[tuple[int, int]] = deque([(start_x, start_y)])
                visited[start_y, start_x] = True
                while queue:
                    x, y = queue.popleft()
                    component.append((x, y))
                    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                        if nx < 0 or ny < 0 or nx >= width or ny >= height:
                            continue
                        neighbor_owner = int(owner_grid[ny, nx])
                        if neighbor_owner == owner and not visited[ny, nx]:
                            visited[ny, nx] = True
                            queue.append((nx, ny))
                        elif neighbor_owner >= 0 and neighbor_owner != owner:
                            neighbor_counts[neighbor_owner] = neighbor_counts.get(neighbor_owner, 0) + 1
                if len(component) <= max_cells and neighbor_counts:
                    target_owner = max(neighbor_counts.items(), key=lambda item: item[1])[0]
                    conversions.append((component, target_owner))

        for component, target_owner in conversions:
            for x, y in component:
                owner_grid[y, x] = target_owner

    def _transfer_state_population(self, state_id: str, loser: str, winner: str) -> None:
        if loser not in self.state.factions or winner not in self.state.factions:
            return
        loss_fraction = min(1.0, max(0.0, self.scenario.physics.state_capture_population_loss_fraction))
        if loss_fraction > 0:
            current_population_pool = self.state.resources.get(loser, 0.0)
            transfer = current_population_pool * loss_fraction
            self.state.resources[loser] = max(0.0, current_population_pool - transfer)
            self.state.resources[winner] = self.state.resources.get(winner, 0.0) + transfer

        convert_fraction = min(1.0, max(0.0, self.scenario.physics.state_capture_unit_fraction))
        state_index = self._state_index_for_id(state_id)
        if convert_fraction <= 0 or state_index is None or self.state.grid.state_id_grid is None:
            return
        candidates = [
            marble
            for marble in self.state.marbles
            if marble.faction_id == loser and self._marble_state_index(marble) == state_index
        ]
        if not candidates:
            return
        convert_count = max(1, int(round(len(candidates) * convert_fraction)))
        for marble in candidates[:convert_count]:
            marble.faction_id = winner
            marble.cooldown_frames = max(marble.cooldown_frames, 30)

    def _current_state_controllers(self, state: MarbleGameState) -> dict[str, str]:
        state_grid = state.grid.state_id_grid
        if state_grid is None or not state.grid.state_records:
            return {}
        controllers: dict[str, str] = {}
        threshold = min(1.0, max(0.1, state.scenario.physics.state_control_threshold))
        for state_index, state_record in enumerate(state.grid.state_records):
            mask = state_grid == state_index
            total = int(mask.sum())
            if total <= 0:
                continue
            owner_values, owner_counts = np_unique_nonnegative(state.grid.owner_grid[mask])
            if not owner_values:
                continue
            top_position = max(range(len(owner_counts)), key=owner_counts.__getitem__)
            if owner_counts[top_position] / total < threshold:
                continue
            faction_id = state.grid.faction_id(owner_values[top_position])
            if faction_id:
                controllers[str(state_record.get("id", state_index))] = faction_id
        return controllers

    def _state_index_for_id(self, state_id: str) -> int | None:
        for index, state_record in enumerate(self.state.grid.state_records):
            if str(state_record.get("id", index)) == state_id:
                return index
        return None

    def _marble_state_index(self, marble: MarbleUnit) -> int | None:
        state_grid = self.state.grid.state_id_grid
        if state_grid is None:
            return None
        gx, gy = self.state.grid.canvas_to_grid(marble.x, marble.y)
        if gy < 0 or gx < 0 or gy >= state_grid.shape[0] or gx >= state_grid.shape[1]:
            return None
        state_index = int(state_grid[gy, gx])
        return state_index if state_index >= 0 else None

    def _resolve_collisions(self) -> None:
        buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        bucket_size = max(14, int(self.scenario.physics.base_radius * 7))
        for index, marble in enumerate(self.state.marbles):
            buckets[(int(marble.x // bucket_size), int(marble.y // bucket_size))].append(index)

        neighbor_offsets = ((0, 0), (1, 0), (0, 1), (1, 1), (-1, 1))
        for bucket, indices in buckets.items():
            bx, by = bucket
            for ox, oy in neighbor_offsets:
                other = buckets.get((bx + ox, by + oy))
                if not other:
                    continue
                if ox == 0 and oy == 0:
                    for left_pos, i in enumerate(indices):
                        for j in indices[left_pos + 1 :]:
                            self._collide_pair(self.state.marbles[i], self.state.marbles[j])
                else:
                    for i in indices:
                        for j in other:
                            self._collide_pair(self.state.marbles[i], self.state.marbles[j])

    def _collide_pair(self, first: MarbleUnit, second: MarbleUnit) -> None:
        dx = second.x - first.x
        dy = second.y - first.y
        distance_sq = dx * dx + dy * dy
        min_distance = first.radius + second.radius
        if distance_sq <= 0 or distance_sq >= min_distance * min_distance:
            return
        distance = math.sqrt(distance_sq)
        nx, ny = dx / distance, dy / distance
        overlap = min_distance - distance
        first.x -= nx * overlap / 2
        first.y -= ny * overlap / 2
        second.x += nx * overlap / 2
        second.y += ny * overlap / 2

        first_normal = first.vx * nx + first.vy * ny
        second_normal = second.vx * nx + second.vy * ny
        first.vx += (second_normal - first_normal) * nx
        first.vy += (second_normal - first_normal) * ny
        second.vx += (first_normal - second_normal) * nx
        second.vy += (first_normal - second_normal) * ny

        if first.faction_id != second.faction_id:
            self._damage_after_collision(first, second)

    def _damage_after_collision(self, first: MarbleUnit, second: MarbleUnit) -> None:
        if first.cooldown_frames or second.cooldown_frames:
            return
        damage = self.scenario.physics.collision_damage
        first_score = first.power * self.state.stat_multiplier(first.faction_id, "power") * self.rng.uniform(0.8, 1.2)
        second_score = second.power * self.state.stat_multiplier(second.faction_id, "power") * self.rng.uniform(0.8, 1.2)
        if first_score >= second_score:
            second.power -= damage
            first.power = min(1.8, first.power + damage * 0.2)
        else:
            first.power -= damage
            second.power = min(1.8, second.power + damage * 0.2)
        if first.power <= 0.25:
            first.faction_id = second.faction_id
            first.power = 0.75
            first.cooldown_frames = 20
        if second.power <= 0.25:
            second.faction_id = first.faction_id
            second.power = 0.75
            second.cooldown_frames = 20

    def _movement_blocker(self, marble: MarbleUnit, x: float, y: float) -> str | None:
        owner_index = self.state.grid.faction_index(marble.faction_id)
        cell_w, cell_h = self.state.grid.cell_size
        grid_h, grid_w = self.state.grid.owner_grid.shape
        for sample_x, sample_y in self._collision_samples(marble, x, y):
            gx = int(sample_x / cell_w)
            gy = int(sample_y / cell_h)
            if gy < 0 or gx < 0 or gy >= grid_h or gx >= grid_w:
                return "land"
            if self.state.grid.province_id_grid[gy, gx] < 0:
                return "land"
            sample_owner = int(self.state.grid.owner_grid[gy, gx])
            if sample_owner < 0 or sample_owner == owner_index:
                continue
            other_faction = self.state.grid.faction_id(sample_owner)
            if other_faction and not self.state.are_allied(marble.faction_id, other_faction):
                return "enemy"
        return None

    def _collision_samples(self, marble: MarbleUnit, x: float, y: float) -> list[tuple[float, float]]:
        samples = [(x, y)]
        radius = marble.radius
        for index in range(12):
            angle = math.tau * index / 12
            samples.append((x + math.cos(angle) * radius, y + math.sin(angle) * radius))
        speed = math.hypot(marble.vx, marble.vy)
        if speed > 0:
            samples.append((x + marble.vx / speed * radius, y + marble.vy / speed * radius))
        return samples

    def _reflection_axes_for_block(
        self,
        marble: MarbleUnit,
        old_x: float,
        old_y: float,
        dt: float,
    ) -> tuple[bool, bool]:
        x_only_blocked = self._movement_blocker(marble, old_x + marble.vx * dt, old_y) is not None
        y_only_blocked = self._movement_blocker(marble, old_x, old_y + marble.vy * dt) is not None
        if x_only_blocked and not y_only_blocked:
            return True, False
        if y_only_blocked and not x_only_blocked:
            return False, True
        return True, True

    def _reflect_from_block(self, marble: MarbleUnit, old_x: float, old_y: float, dt: float) -> None:
        reflect_x, reflect_y = self._reflection_axes_for_block(marble, old_x, old_y, dt)
        self._reflect_axes(marble, reflect_x=reflect_x, reflect_y=reflect_y, steer=True)

    def _reflect_axes(self, marble: MarbleUnit, reflect_x: bool, reflect_y: bool, steer: bool = False) -> None:
        if reflect_x:
            marble.vx *= -1
        if reflect_y:
            marble.vy *= -1
        jitter = self.rng.uniform(-self.scenario.physics.bounce_jitter, self.scenario.physics.bounce_jitter)
        angle = math.atan2(marble.vy, marble.vx) + jitter
        if steer:
            angle = self._strategic_bounce_angle(marble, angle)
        speed = max(self.scenario.physics.min_speed, math.hypot(marble.vx, marble.vy))
        marble.vx = math.cos(angle) * speed
        marble.vy = math.sin(angle) * speed

    def _strategic_bounce_angle(self, marble: MarbleUnit, reflected_angle: float) -> float:
        target_faction = self.scenario.strategic_targets.get(marble.faction_id)
        strength = min(0.75, max(0.0, self.scenario.physics.strategic_bounce_strength))
        if not target_faction or strength <= 0 or target_faction not in self.state.grid.faction_ids:
            return reflected_angle
        target_center = self._owned_centroid(target_faction)
        if target_center is None:
            return reflected_angle
        target_x, target_y = target_center
        target_angle = math.atan2(target_y - marble.y, target_x - marble.x)
        blended_x = math.cos(reflected_angle) * (1.0 - strength) + math.cos(target_angle) * strength
        blended_y = math.sin(reflected_angle) * (1.0 - strength) + math.sin(target_angle) * strength
        if abs(blended_x) < 0.0001 and abs(blended_y) < 0.0001:
            return reflected_angle
        return math.atan2(blended_y, blended_x)

    def _owned_centroid(self, faction_id: str) -> tuple[float, float] | None:
        owner_index = self.state.grid.faction_index(faction_id)
        ys, xs = np.where(self.state.grid.owner_grid == owner_index)
        if len(xs) == 0:
            return None
        cell_w, cell_h = self.state.grid.cell_size
        return (float(xs.mean()) + 0.5) * cell_w, (float(ys.mean()) + 0.5) * cell_h

    def _marble_count(self, faction_id: str) -> int:
        return sum(1 for marble in self.state.marbles if marble.faction_id == faction_id)

    def _population_weight(self, faction_id: str) -> float:
        faction = self.state.factions.get(faction_id) if hasattr(self, "state") else self.scenario.factions.get(faction_id)
        if faction is None:
            return 1.0
        if faction.population_spawn_weight is not None:
            return max(0.1, float(faction.population_spawn_weight))
        populations = [item.population for item in self.scenario.factions.values() if item.population]
        if not faction.population or not populations:
            return 1.0
        return max(0.1, math.sqrt(faction.population / max(populations)))

    def _population_capacity_scale(self, faction_id: str) -> float:
        return 0.35 + self._population_weight(faction_id) * 0.65

    def _population_growth_multiplier(self) -> float:
        years_elapsed = max(0, self.state.current_year - self.scenario.start_year)
        rate = max(0.0, self.scenario.physics.population_growth_rate_per_year)
        cap = max(1.0, self.scenario.physics.max_population_growth_multiplier)
        return min(cap, math.exp(rate * years_elapsed))

    def _initial_marble_count(self, faction_id: str) -> int:
        base = self.scenario.physics.initial_marbles_per_faction
        return max(1, int(round(base * self._population_capacity_scale(faction_id))))

    def _max_marble_count(self, faction_id: str) -> int:
        base = self.scenario.physics.max_marbles_per_faction
        scaled = base * self._population_capacity_scale(faction_id)
        start_fraction = min(1.0, max(0.05, self.scenario.physics.max_units_start_fraction))
        progress = min(1.0, max(0.0, self.state.frame / max(1, self.state.total_frames)))
        growth_power = max(0.1, self.scenario.physics.max_units_growth_power)
        timeline_scale = start_fraction + (1.0 - start_fraction) * progress**growth_power
        scaled *= timeline_scale
        scaled *= self.state.stat_multiplier(faction_id, "max_units")
        return max(self._initial_marble_count(faction_id), int(round(scaled)))


def np_unique_nonnegative(values: np.ndarray) -> tuple[list[int], list[int]]:
    filtered = values[values >= 0]
    if filtered.size == 0:
        return [], []
    unique_values, counts = np.unique(filtered, return_counts=True)
    return [int(value) for value in unique_values], [int(count) for count in counts]
