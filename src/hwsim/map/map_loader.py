from __future__ import annotations

from pathlib import Path

from hwsim.core.models import EventConfig, MapConfig, ScenarioConfig, StyleConfig
from hwsim.utils.file_utils import read_json, resolve_path


def load_scenario(path: str | Path) -> ScenarioConfig:
    scenario_path = resolve_path(path)
    scenario = ScenarioConfig.model_validate(read_json(scenario_path))
    for faction_id, faction in scenario.factions.items():
        faction.id = faction_id
    return scenario


def load_map(path: str | Path) -> MapConfig:
    map_config = MapConfig.model_validate(read_json(resolve_path(path)))
    ids = {region.id for region in map_config.regions}
    for region in map_config.regions:
        missing = set(region.neighbors) - ids
        if missing:
            raise ValueError(f"Region {region.id} references missing neighbors: {sorted(missing)}")
    return map_config


def load_events(path: str | Path) -> EventConfig:
    return EventConfig.model_validate(read_json(resolve_path(path)))


def load_style(path: str | Path) -> StyleConfig:
    return StyleConfig.model_validate(read_json(resolve_path(path)))


def load_bundle(scenario_path: str | Path) -> tuple[ScenarioConfig, MapConfig, EventConfig, StyleConfig]:
    scenario = load_scenario(scenario_path)
    map_config = load_map(scenario.map_file)
    event_config = load_events(scenario.event_file)
    style_config = load_style(scenario.style_file)
    return scenario, map_config, event_config, style_config
