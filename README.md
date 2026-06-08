# Historical War Sim

A local Python project for historical war simulation videos and game-like map
simulations. It currently supports two modes:

- Scripted Three Kingdoms video generation with historical event overlays.
- Marble-style real-map simulation where moving balls represent units, colored
  cells represent territory, and physics/randomness drives conquest.

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

Export a one-minute, 60fps Marble-mode video:

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

The default Wei/Shu/Wu ball production uses Three Kingdoms registered
population figures as configurable gameplay weights: Cao Wei 4,432,881,
Shu Han 1,082,000, and Eastern Wu 2,535,000. These weights affect initial
balls, resource income, and readable max unit caps; they are not exact
historical population reconstruction.

## Development

```bash
.venv/bin/python -m pytest
.venv/bin/python scripts/run_simulation.py --scenario configs/scenarios/sanguo_shu_unification_demo.json
```

The implementation lives under `src/hwsim`. Top-level scripts are intentionally
thin wrappers around package code.
