from __future__ import annotations

import math
import random
from collections import defaultdict
from pathlib import Path

from hwsim.core.models import EventConfig
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
        for faction_id in faction_ids:
            for _ in range(self._initial_marble_count(faction_id)):
                self._spawn_marble(state, faction_id, free=True)
        return state

    def _spawn_resources(self) -> None:
        if self.state.frame % max(1, self.scenario.physics.spawn_interval_frames) != 0:
            return
        counts = self.state.grid.owned_cell_counts()
        for faction_id, count in counts.items():
            spawn_rate = self.state.stat_multiplier(faction_id, "spawn_rate")
            population_income = 0.55 + self._population_weight(faction_id) * 0.65
            income = count / 100 * self.scenario.physics.resource_gain_per_100_cells * spawn_rate * population_income
            self.state.resources[faction_id] = self.state.resources.get(faction_id, 0) + income
            while (
                self.state.resources[faction_id] >= self.scenario.physics.spawn_cost
                and self._marble_count(faction_id) < self._max_marble_count(faction_id)
            ):
                if self._spawn_marble(self.state, faction_id, free=False):
                    self.state.resources[faction_id] -= self.scenario.physics.spawn_cost
                else:
                    break

    def _consume_pending_ball_adds(self) -> None:
        if not self.state.pending_ball_adds:
            return
        pending = self.state.pending_ball_adds
        self.state.pending_ball_adds = []
        for faction_id, amount in pending:
            for _ in range(amount):
                if self._marble_count(faction_id) >= self._max_marble_count(faction_id):
                    break
                if not self._spawn_marble(self.state, faction_id, free=True):
                    break

    def _spawn_marble(self, state: MarbleGameState, faction_id: str, free: bool) -> bool:
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
                self._reflect_axes(marble, reflect_x=hit_x, reflect_y=hit_y)
                continue

            blocker = self._movement_blocker(marble, next_x, next_y)
            if blocker == "enemy":
                marble.x, marble.y = old_x, old_y
                reflect_x, reflect_y = self._reflection_axes_for_block(marble, old_x, old_y, effective_dt)
                self._capture_cells_for_marble(marble, center=(next_x, next_y))
                self._reflect_axes(marble, reflect_x=reflect_x, reflect_y=reflect_y)
            elif blocker == "land":
                marble.x, marble.y = old_x, old_y
                self._reflect_from_block(marble, old_x, old_y, effective_dt)
            else:
                marble.x, marble.y = next_x, next_y

    def _capture_cells(self) -> None:
        for marble in self.state.marbles:
            self._capture_cells_for_marble(marble)

    def _capture_cells_for_marble(self, marble: MarbleUnit, center: tuple[float, float] | None = None) -> None:
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
        self._reflect_axes(marble, reflect_x=reflect_x, reflect_y=reflect_y)

    def _reflect_axes(self, marble: MarbleUnit, reflect_x: bool, reflect_y: bool) -> None:
        if reflect_x:
            marble.vx *= -1
        if reflect_y:
            marble.vy *= -1
        jitter = self.rng.uniform(-self.scenario.physics.bounce_jitter, self.scenario.physics.bounce_jitter)
        angle = math.atan2(marble.vy, marble.vx) + jitter
        speed = max(self.scenario.physics.min_speed, math.hypot(marble.vx, marble.vy))
        marble.vx = math.cos(angle) * speed
        marble.vy = math.sin(angle) * speed

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

    def _initial_marble_count(self, faction_id: str) -> int:
        base = self.scenario.physics.initial_marbles_per_faction
        return max(1, int(round(base * self._population_capacity_scale(faction_id))))

    def _max_marble_count(self, faction_id: str) -> int:
        base = self.scenario.physics.max_marbles_per_faction
        scaled = base * self._population_capacity_scale(faction_id)
        scaled *= self.state.stat_multiplier(faction_id, "max_units")
        return max(self._initial_marble_count(faction_id), int(round(scaled)))
