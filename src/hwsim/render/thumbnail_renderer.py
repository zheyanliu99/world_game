from __future__ import annotations

from pathlib import Path

from hwsim.core.models import MapConfig, SimulationResult, StyleConfig
from hwsim.render.frame_renderer import FrameRenderer


def render_thumbnail(
    result: SimulationResult,
    map_config: MapConfig,
    style: StyleConfig,
    output_path: str | Path,
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    renderer = FrameRenderer(map_config, style)
    image = renderer.render_thumbnail(result.final_state, result.scenario.title)
    image.save(output_path)

