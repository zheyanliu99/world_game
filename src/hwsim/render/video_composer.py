from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg


def compose_video(
    frames_dir: str | Path,
    output_path: str | Path,
    render_fps: int,
    output_fps: int,
) -> None:
    frames_dir = Path(frames_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-y",
        "-framerate",
        str(render_fps),
        "-i",
        str(frames_dir / "frame_%06d.png"),
        "-r",
        str(output_fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(f"FFmpeg failed with code {completed.returncode}:\n{completed.stderr}")

