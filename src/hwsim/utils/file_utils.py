from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_path(path: str | Path, base_dir: Path | None = None) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return (base_dir or project_root() / path).resolve()


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def ensure_dir(path: str | Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_png_frames(frames_dir: str | Path) -> Path:
    frames_dir = ensure_dir(frames_dir)
    for frame in frames_dir.glob("frame_*.png"):
        frame.unlink()
    return frames_dir


def copy_file(source: str | Path, destination: str | Path) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
