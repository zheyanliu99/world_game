from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from hwsim.utils.file_utils import read_json, resolve_path


CITY_GRAPH_FILE = Path("configs/maps/sanguo_cities.json")
GENERAL_SEED_FILE = Path("data/generals/general_seed.json")


class CityConfig(BaseModel):
    id: str
    name_cn: str
    region_id: str
    position: tuple[float, float]
    terrain: str = "plain"
    population: int = 40
    economy: int = 40
    fort: float = 1.0
    initial_owner: str
    neighbors: list[str] = Field(default_factory=list)


class RoadConfig(BaseModel):
    from_city_id: str
    to_city_id: str
    route_type: str = "road"
    distance_km: int = 80
    food_cost: int = 2
    gold_cost: int = 0
    soldier_loss_bps: int = 0
    readiness_cost: int = 4
    source_note: str = ""

    def connects(self, city_a: str, city_b: str) -> bool:
        return {self.from_city_id, self.to_city_id} == {city_a, city_b}


class CityGraphConfig(BaseModel):
    canvas_size: tuple[int, int]
    cities: list[CityConfig]
    roads: list[RoadConfig] = Field(default_factory=list)

    def city_map(self) -> dict[str, CityConfig]:
        return {city.id: city for city in self.cities}

    def road_map(self) -> dict[tuple[str, str], RoadConfig]:
        return {tuple(sorted((road.from_city_id, road.to_city_id))): road for road in self.roads}


class GeneralSeed(BaseModel):
    id: str
    name_cn: str
    name_en: str
    faction_id: str
    portrait_path: str
    starting_city_id: str
    soldiers: int
    max_soldiers: int
    command: int
    attack: int
    defense: int
    mobility: int
    loyalty: int
    food_need: int
    surrender_risk: float

    @field_validator("command", "attack", "defense", "mobility", "loyalty")
    @classmethod
    def _stat_range(cls, value: int) -> int:
        if value < 1 or value > 100:
            raise ValueError("general stats must be 1-100")
        return value

    @field_validator("soldiers", "max_soldiers", "food_need")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("soldier and food fields must be positive")
        return value

    @field_validator("surrender_risk")
    @classmethod
    def _risk_range(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("surrender risk must be 0-1")
        return value


def load_city_graph(path: str | Path = CITY_GRAPH_FILE) -> CityGraphConfig:
    graph = CityGraphConfig.model_validate(read_json(resolve_path(path)))
    _derive_neighbors_from_roads(graph)
    validate_city_graph(graph)
    return graph


def load_general_seeds(path: str | Path = GENERAL_SEED_FILE, city_ids: set[str] | None = None) -> list[GeneralSeed]:
    seeds = [GeneralSeed.model_validate(item) for item in read_json(resolve_path(path))]
    validate_general_seeds(seeds, city_ids=city_ids)
    return seeds


def validate_city_graph(graph: CityGraphConfig) -> None:
    cities = graph.city_map()
    if len(cities) != len(graph.cities):
        raise ValueError("city ids must be unique")
    seen_roads: set[tuple[str, str]] = set()
    for road in graph.roads:
        if road.from_city_id == road.to_city_id:
            raise ValueError(f"Road cannot connect {road.from_city_id} to itself")
        if road.from_city_id not in cities:
            raise ValueError(f"Road references missing city {road.from_city_id}")
        if road.to_city_id not in cities:
            raise ValueError(f"Road references missing city {road.to_city_id}")
        key = tuple(sorted((road.from_city_id, road.to_city_id)))
        if key in seen_roads:
            raise ValueError(f"Duplicate road {key[0]}-{key[1]}")
        seen_roads.add(key)
        if road.distance_km <= 0:
            raise ValueError(f"Road {key[0]}-{key[1]} needs positive distance")
    for city in graph.cities:
        for neighbor_id in city.neighbors:
            neighbor = cities.get(neighbor_id)
            if neighbor is None:
                raise ValueError(f"City {city.id} references missing neighbor {neighbor_id}")
            if city.id not in neighbor.neighbors:
                raise ValueError(f"City adjacency must be symmetric: {city.id} -> {neighbor_id}")
            if tuple(sorted((city.id, neighbor_id))) not in seen_roads:
                raise ValueError(f"City adjacency must have a road: {city.id} -> {neighbor_id}")


def _derive_neighbors_from_roads(graph: CityGraphConfig) -> None:
    if not graph.roads:
        return
    neighbors = {city.id: set[str]() for city in graph.cities}
    for road in graph.roads:
        neighbors.setdefault(road.from_city_id, set()).add(road.to_city_id)
        neighbors.setdefault(road.to_city_id, set()).add(road.from_city_id)
    for city in graph.cities:
        city.neighbors = sorted(neighbors.get(city.id, set()))


def validate_general_seeds(seeds: list[GeneralSeed], city_ids: set[str] | None = None) -> None:
    seen: set[str] = set()
    for seed in seeds:
        if seed.id in seen:
            raise ValueError(f"Duplicate general id {seed.id}")
        seen.add(seed.id)
        if seed.soldiers > seed.max_soldiers:
            raise ValueError(f"{seed.id} starts above max soldiers")
        if city_ids is not None and seed.starting_city_id not in city_ids:
            raise ValueError(f"{seed.id} starts in unknown city {seed.starting_city_id}")
        portrait = seed.portrait_path.removeprefix("/static/")
        portrait_path = resolve_path(Path("src/hwsim/web/static") / portrait)
        if not portrait_path.exists():
            raise ValueError(f"{seed.id} portrait missing: {seed.portrait_path}")
