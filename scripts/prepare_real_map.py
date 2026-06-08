#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hwsim.map.real_map import prepare_real_map  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and rasterize a real map for Marble mode.")
    parser.add_argument("--config", required=True, help="Path to real-map config JSON.")
    parser.add_argument("--force", action="store_true", help="Redownload and rebuild the prepared map.")
    args = parser.parse_args()

    prepared_path = prepare_real_map(args.config, force=args.force)
    print(f"Prepared map: {prepared_path}")


if __name__ == "__main__":
    main()

