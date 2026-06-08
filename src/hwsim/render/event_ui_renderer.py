from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFont

from hwsim.core.models import GameState, StyleConfig, TriggeredEvent


def draw_event_overlay(
    image: Image.Image,
    state: GameState,
    event: TriggeredEvent,
    style: StyleConfig,
    title_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    subtitle_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    progress: float,
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    effect = event.effect
    if effect == "fire_overlay":
        _draw_fire_overlay(draw, state, event, progress)
    elif effect == "edict_overlay":
        _draw_edict_overlay(draw, image.size, progress)
    elif effect == "marching_arrows":
        _draw_marching_arrows(draw, state, event, progress)
    elif effect == "collapse_flash":
        alpha = int(70 * (1 - min(1.0, progress)))
        draw.rectangle([(0, 0), image.size], fill=(150, 35, 30, max(20, alpha)))

    _draw_event_banner(draw, image.size, event, style, title_font, subtitle_font)
    image.alpha_composite(overlay)


def _draw_event_banner(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    event: TriggeredEvent,
    style: StyleConfig,
    title_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    subtitle_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    width, height = size
    banner = (width // 2 - 500, 115, width // 2 + 500, 270)
    draw.rounded_rectangle(banner, radius=8, fill=(24, 18, 12, 220), outline=(238, 204, 130, 220), width=3)
    _centered_text(draw, event.title, (width // 2, 162), title_font, style.event_title)
    _centered_text(draw, event.subtitle, (width // 2, 222), subtitle_font, style.event_subtitle)


def _draw_fire_overlay(
    draw: ImageDraw.ImageDraw,
    state: GameState,
    event: TriggeredEvent,
    progress: float,
) -> None:
    centers = _focus_centers(state, event)
    rng = random.Random(event.event_id)
    pulse = 0.75 + 0.25 * math.sin(progress * math.tau * 2)
    for center_x, center_y in centers:
        for _ in range(34):
            radius = rng.randint(24, 96)
            offset_x = rng.randint(-130, 130)
            offset_y = rng.randint(-80, 80)
            alpha = int(rng.randint(45, 120) * pulse)
            color = rng.choice([(230, 72, 28, alpha), (255, 150, 40, alpha), (255, 210, 70, alpha)])
            box = [
                center_x + offset_x - radius,
                center_y + offset_y - radius // 2,
                center_x + offset_x + radius,
                center_y + offset_y + radius // 2,
            ]
            draw.ellipse(box, fill=color)


def _draw_edict_overlay(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    progress: float,
) -> None:
    width, height = size
    glow = int(52 + 24 * math.sin(progress * math.tau))
    draw.rectangle([(width // 2 - 585, 305), (width // 2 + 585, height - 205)], fill=(157, 112, 42, glow))
    draw.rounded_rectangle(
        (width // 2 - 420, 335, width // 2 + 420, height - 245),
        radius=8,
        fill=(205, 174, 102, 72),
        outline=(255, 228, 144, 120),
        width=3,
    )


def _draw_marching_arrows(
    draw: ImageDraw.ImageDraw,
    state: GameState,
    event: TriggeredEvent,
    progress: float,
) -> None:
    centers = _focus_centers(state, event)
    if len(centers) < 2:
        return
    start = centers[0]
    end = centers[-1]
    steps = 5
    for index in range(steps):
        phase = (progress + index / steps) % 1.0
        x = start[0] + (end[0] - start[0]) * phase
        y = start[1] + (end[1] - start[1]) * phase
        _arrow(draw, (x - 45, y - 18), (x + 45, y + 18), fill=(255, 222, 118, 190))


def _arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    fill: tuple[int, int, int, int],
) -> None:
    draw.line([start, end], fill=fill, width=8)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head = 18
    points = [
        end,
        (end[0] - head * math.cos(angle - 0.55), end[1] - head * math.sin(angle - 0.55)),
        (end[0] - head * math.cos(angle + 0.55), end[1] - head * math.sin(angle + 0.55)),
    ]
    draw.polygon(points, fill=fill)


def _focus_centers(state: GameState, event: TriggeredEvent) -> list[tuple[int, int]]:
    centers = []
    for region_id in event.focus_regions:
        region = state.regions.get(region_id)
        if region:
            centers.append(region.center)
    return centers


def _centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    center: tuple[int, int],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text((center[0] - width / 2, center[1] - height / 2), text, font=font, fill=fill)

