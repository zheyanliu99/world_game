#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hwsim.render.video_composer import compose_video  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Compose an MP4 from rendered PNG frames.")
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--render-fps", type=int, default=6)
    parser.add_argument("--output-fps", type=int, default=30)
    args = parser.parse_args()
    compose_video(args.frames_dir, args.output, args.render_fps, args.output_fps)
    print(f"Video: {args.output}")


if __name__ == "__main__":
    main()

