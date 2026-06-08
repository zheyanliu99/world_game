#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hwsim.game.marble_exporter import generate_marble_demo  # noqa: E402
from hwsim.game.marble_renderer import MarbleRenderer, marble_subtitles  # noqa: E402
from hwsim.map.map_loader import load_style  # noqa: E402
from hwsim.physics.simulator import MarbleSimulator, load_marble_game_inputs  # noqa: E402
from hwsim.render.frame_renderer import subtitle_at  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Play the Marble-style Three Kingdoms simulation.")
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()

    import pygame

    scenario_path = Path(args.scenario)
    scenario, prepared_map, event_config = load_marble_game_inputs(scenario_path, prepare_map=True)
    style = load_style(scenario.style_file)
    simulator = MarbleSimulator(scenario, prepared_map, event_config)
    renderer = MarbleRenderer(style)

    pygame.init()
    screen = pygame.display.set_mode(simulator.state.grid.canvas_size)
    pygame.display.set_caption("Historical War Sim - Marble Mode")
    clock = pygame.time.Clock()

    paused = False
    speed = 1
    seed_offset = 0
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    paused = not paused
                elif event.key in (pygame.K_EQUALS, pygame.K_PLUS):
                    speed = min(8, speed + 1)
                elif event.key == pygame.K_MINUS:
                    speed = max(1, speed - 1)
                elif event.key == pygame.K_r:
                    seed_offset += 1
                    scenario.random_seed += seed_offset
                    simulator = MarbleSimulator(scenario, prepared_map, event_config)
                elif event.key == pygame.K_s:
                    image = renderer.render(simulator.state, speed_label=f"{speed}x")
                    out = ROOT / "outputs" / "thumbnails" / f"{scenario.output_basename}_screenshot.png"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    image.save(out)
                    print(f"Screenshot: {out}")
                elif event.key == pygame.K_e:
                    result = generate_marble_demo(scenario_path, ROOT / "outputs")
                    print(f"Exported: {result['video_path']}")

        if not paused:
            simulator.step(speed)
        subtitles = marble_subtitles(simulator.state)
        text = subtitle_at(subtitles, simulator.state.seconds)
        image = renderer.render(simulator.state, subtitle=text, speed_label=f"{speed}x")
        surface = pygame.image.frombuffer(image.tobytes(), image.size, "RGB")
        screen.blit(surface, (0, 0))
        pygame.display.flip()
        clock.tick(scenario.physics.fps)

    pygame.quit()


if __name__ == "__main__":
    main()

