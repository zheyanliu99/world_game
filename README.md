# Historical War Sim

A local Python demo that generates a simplified Three Kingdoms historical war
simulation video. The first scenario is a configurable alternate-history arc
where 蜀汉 survives the early game, benefits from 赤壁 and 北伐 windows, and is
softly biased toward unification.

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

## Development

```bash
.venv/bin/python -m pytest
.venv/bin/python scripts/run_simulation.py --scenario configs/scenarios/sanguo_shu_unification_demo.json
```

The implementation lives under `src/hwsim`. Top-level scripts are intentionally
thin wrappers around package code.
