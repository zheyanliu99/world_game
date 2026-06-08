from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg

from hwsim.core.models import SubtitleEntry
from hwsim.map.map_loader import load_style
from hwsim.physics.simulator import MarbleSimulator, load_marble_game_inputs
from hwsim.game.marble_renderer import MarbleRenderer, marble_subtitles
from hwsim.render.frame_renderer import subtitle_at
from hwsim.render.subtitle_writer import write_srt
from hwsim.utils.file_utils import copy_file, ensure_dir


def generate_marble_demo(scenario_path: str | Path, output_root: str | Path) -> dict[str, Path | int]:
    scenario_path = Path(scenario_path)
    output_root = Path(output_root)
    scenario, prepared_map, event_config = load_marble_game_inputs(scenario_path, prepare_map=True)
    style = load_style(scenario.style_file)
    simulator = MarbleSimulator(scenario, prepared_map, event_config)
    renderer = MarbleRenderer(style)

    video_path = output_root / "videos" / f"{scenario.output_basename}.mp4"
    subtitle_path = output_root / "subtitles" / f"{scenario.output_basename}.srt"
    thumbnail_path = output_root / "thumbnails" / f"{scenario.output_basename}.png"
    config_copy_path = output_root / f"{scenario.output_basename}_config.json"
    ensure_dir(video_path.parent)
    ensure_dir(subtitle_path.parent)
    ensure_dir(thumbnail_path.parent)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    width, height = simulator.state.grid.canvas_size
    command = [
        ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(scenario.render_fps),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(scenario.output_fps),
        "-movflags",
        "+faststart",
        str(video_path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdin is None:
        raise RuntimeError("Failed to open FFmpeg stdin")

    subtitles: list[SubtitleEntry] = [
        SubtitleEntry(0, min(4.5, scenario.video_length_seconds), scenario.intro_narration)
    ]
    total_frames = int(scenario.video_length_seconds * scenario.render_fps)
    last_image = None
    for _frame in range(total_frames):
        text = subtitle_at(subtitles + marble_subtitles(simulator.state), simulator.state.seconds)
        image = renderer.render(simulator.state, subtitle=text)
        process.stdin.write(image.tobytes())
        last_image = image
        simulator.step()

    process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"FFmpeg failed with code {return_code}:\n{stderr}")

    final_subtitles = marble_subtitles(simulator.state)
    write_srt(final_subtitles, subtitle_path)
    if last_image is None:
        last_image = renderer.render(simulator.state)
    last_image.save(thumbnail_path)
    copy_file(scenario_path, config_copy_path)

    return {
        "frames": total_frames,
        "video_path": video_path,
        "subtitle_path": subtitle_path,
        "thumbnail_path": thumbnail_path,
        "config_copy_path": config_copy_path,
    }

