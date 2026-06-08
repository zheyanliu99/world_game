#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hwsim.core.simulator import Simulator  # noqa: E402
from hwsim.map.map_loader import load_bundle  # noqa: E402
from hwsim.render.frame_renderer import render_result_frames  # noqa: E402
from hwsim.render.subtitle_writer import write_srt  # noqa: E402
from hwsim.render.thumbnail_renderer import render_thumbnail  # noqa: E402
from hwsim.render.video_composer import compose_video  # noqa: E402
from hwsim.utils.file_utils import copy_file, ensure_dir  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Three Kingdoms demo video.")
    parser.add_argument("--scenario", required=True, help="Path to the scenario JSON file.")
    args = parser.parse_args()

    scenario_path = Path(args.scenario)
    scenario, map_config, event_config, style = load_bundle(scenario_path)
    result = Simulator(scenario, map_config, event_config).run()

    output_root = ROOT / "outputs"
    frames_dir = output_root / "frames" / scenario.output_basename
    video_path = output_root / "videos" / f"{scenario.output_basename}.mp4"
    subtitle_path = output_root / "subtitles" / f"{scenario.output_basename}.srt"
    thumbnail_path = output_root / "thumbnails" / f"{scenario.output_basename}.png"
    config_copy_path = output_root / f"{scenario.output_basename}_config.json"

    ensure_dir(output_root)
    write_srt(result.subtitles, subtitle_path)
    frame_count = render_result_frames(result, map_config, style, frames_dir)
    compose_video(frames_dir, video_path, scenario.render_fps, scenario.output_fps)
    render_thumbnail(result, map_config, style, thumbnail_path)
    copy_file(scenario_path, config_copy_path)

    print(f"Generated {frame_count} frames")
    print(f"Video: {video_path}")
    print(f"Subtitles: {subtitle_path}")
    print(f"Thumbnail: {thumbnail_path}")
    print(f"Config copy: {config_copy_path}")


if __name__ == "__main__":
    main()

