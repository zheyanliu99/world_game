#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hwsim.core.game_state import faction_ranking  # noqa: E402
from hwsim.core.simulator import Simulator  # noqa: E402
from hwsim.map.map_loader import load_bundle  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic Three Kingdoms simulation.")
    parser.add_argument("--scenario", required=True, help="Path to a scenario JSON file.")
    args = parser.parse_args()

    scenario, map_config, event_config, _style = load_bundle(args.scenario)
    result = Simulator(scenario, map_config, event_config).run()

    print(f"Scenario: {scenario.title}")
    print(f"Years: {scenario.start_year}-{scenario.end_year}")
    print("Triggered events:")
    for event in result.triggered_events:
        print(f"  {event.year}: {event.name_cn}")
    print("Final ranking:")
    for faction_id, count in faction_ranking(result.final_state)[:6]:
        faction = result.final_state.factions[faction_id]
        print(f"  {faction.display_name(result.final_state.current_year)}: {count} regions, {faction.troops} troops")


if __name__ == "__main__":
    main()

