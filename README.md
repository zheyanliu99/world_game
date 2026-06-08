# Historical War Sim

A local Python project for historical war simulation videos and game-like map
simulations. It currently supports two modes:

- Scripted Three Kingdoms video generation with historical event overlays.
- Marble-style real-map simulation where moving population units represent
  people/armies, colored cells represent territory, and physics/randomness
  drives conquest.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/generate_demo.py --scenario configs/scenarios/sanguo_shu_unification_demo.json
```

Expected outputs:

```text
outputs/videos/sanguo_demo_001.mp4
outputs/subtitles/sanguo_demo_001.srt
outputs/thumbnails/sanguo_demo_001.png
outputs/sanguo_demo_001_config.json
```

The MP4 composer uses the `imageio-ffmpeg` bundled binary, so a system `ffmpeg`
install is not required.

## Marble Real-Map Mode

Prepare the real China ADM1 map cache:

```bash
.venv/bin/python scripts/prepare_real_map.py --config configs/maps/sanguo_real_map.json
```

Play the simulation locally:

```bash
.venv/bin/python scripts/play_marble_sim.py --scenario configs/scenarios/sanguo_marble_real_map_demo.json
```

Controls:

```text
Space  pause/resume
+ / -  speed up / slow down
R      reroll with a new seed
S      save screenshot
E      export configured run
Esc    quit
```

Export a three-minute, 60fps Marble-mode video:

```bash
.venv/bin/python scripts/generate_demo.py --scenario configs/scenarios/sanguo_marble_real_map_demo.json
```

Expected Marble outputs:

```text
outputs/videos/sanguo_marble_demo_001.mp4
outputs/subtitles/sanguo_marble_demo_001.srt
outputs/thumbnails/sanguo_marble_demo_001.png
outputs/sanguo_marble_demo_001_config.json
```

Map boundaries are downloaded from geoBoundaries gbOpen China ADM1 and cached
under `data/maps/`, which is ignored by git. Attribution: geoBoundaries
CC BY 4.0, William & Mary geoLab.

The default Wei/Shu/Wu population-unit production uses Three Kingdoms
registered population figures as configurable gameplay weights: Cao Wei
4,432,881, Shu Han 1,082,000, and Eastern Wu 2,535,000. These weights affect
initial population units, resource income, and readable max unit caps; they are
not exact historical population reconstruction.

The default Marble event timeline also includes incident and betrayal effects.
Betrayal/surrender incidents can flip nearby cells and convert nearby population
units to another kingdom, while temporary speed boosts affect units already on
the map and draw short wind trails. Ball capacity ramps upward with exponential
population recovery, so the screen fills as years pass. If a kingdom drops below
10% of total land, it can no longer capture new territory and starts losing
population units and population resources until it recovers or dies out.

The real map is also grouped into coarse historical 州 overlays such as 益州,
荆州, 扬州, and 凉州. Thin state borders and state labels are rendered on the map.
If one kingdom controls more than 80% of a state, that state adds extra linear
population income on top of the global exponential recovery. Capital labels
update by year and sit at the center of each kingdom's largest connected
territory, rather than a fixed province. Strategic bounce steering can bend a
non-enemy-wall rebound toward a configured enemy target, and timed alliances can
prevent allied border capture until an event breaks the alliance.
When control of a state flips, the losing kingdom drops a configurable
percentage of its population resource pool, and the winning kingdom gains that
same amount.

## Development

```bash
.venv/bin/python -m pytest
.venv/bin/python scripts/run_simulation.py --scenario configs/scenarios/sanguo_shu_unification_demo.json
```

The implementation lives under `src/hwsim`. Top-level scripts are intentionally
thin wrappers around package code.
