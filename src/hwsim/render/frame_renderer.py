from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from hwsim.core.game_state import faction_ranking
from hwsim.core.models import GameState, MapConfig, SimulationResult, StyleConfig, SubtitleEntry, TriggeredEvent
from hwsim.narration.script_generator import year_to_seconds
from hwsim.render.event_ui_renderer import draw_event_overlay
from hwsim.utils.file_utils import clean_png_frames


class FrameRenderer:
    def __init__(self, map_config: MapConfig, style: StyleConfig) -> None:
        self.map_config = map_config
        self.style = style
        self.size = style.canvas_size
        self.background = self._make_background()
        self.fonts = {
            "year": self._font(54),
            "ranking": self._font(30),
            "label": self._font(28),
            "small": self._font(22),
            "subtitle": self._font(38),
            "event_title": self._font(54),
            "event_subtitle": self._font(34),
            "thumbnail": self._font(56),
        }

    def render_frame(
        self,
        state: GameState,
        active_event: TriggeredEvent | None = None,
        subtitle: str = "",
        event_progress: float = 0.0,
    ) -> Image.Image:
        image = self.background.copy().convert("RGBA")
        draw = ImageDraw.Draw(image)
        self._draw_regions(draw, state)
        self._draw_hud(draw, state)
        if active_event:
            draw_event_overlay(
                image,
                state,
                active_event,
                self.style,
                self.fonts["event_title"],
                self.fonts["event_subtitle"],
                event_progress,
            )
        if subtitle:
            self._draw_subtitle(image, subtitle)
        return image.convert("RGB")

    def render_thumbnail(self, state: GameState, title: str) -> Image.Image:
        image = self.render_frame(state)
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle([(0, 0), image.size], fill=(0, 0, 0, 55))
        draw.rounded_rectangle((260, 118, 1390, 255), radius=8, fill=(12, 10, 8, 190), outline=(238, 204, 130, 150), width=2)
        wrapped = self._wrap_text(draw, title, self.fonts["thumbnail"], 1040)
        y = 155
        for line in wrapped[:2]:
            self._centered_text(draw, line, (835, y), self.fonts["thumbnail"], "#FFE7A0")
            y += 58
        image = image.convert("RGBA")
        image.alpha_composite(overlay)
        return image.convert("RGB")

    def _make_background(self) -> Image.Image:
        width, height = self.size
        base = np.zeros((height, width, 3), dtype=np.uint8)
        bg = _hex_to_rgb(self.style.background)
        paper = _hex_to_rgb(self.style.paper)
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 7, (height, width, 1))
        gradient = np.linspace(0, 1, width, dtype=np.float32)[None, :, None]
        color = np.array(bg) * (0.72 + 0.12 * gradient) + np.array(paper) * (0.28 - 0.08 * gradient)
        base[:, :, :] = np.clip(color + noise, 0, 255)
        return Image.fromarray(base, mode="RGB")

    def _draw_regions(self, draw: ImageDraw.ImageDraw, state: GameState) -> None:
        for region in self.map_config.regions:
            owner_id = state.region_owners[region.id]
            faction = state.factions[owner_id]
            fill = _hex_to_rgba(faction.color, 210 if faction.alive else 150)
            border = _hex_to_rgba(self.style.border, 225)
            draw.polygon(region.polygon, fill=fill, outline=border)
            draw.line(region.polygon + [region.polygon[0]], fill=border, width=3)

        for region in self.map_config.regions:
            owner_id = state.region_owners[region.id]
            faction = state.factions[owner_id]
            label = f"{region.name_cn}\n{faction.display_name(state.current_year)}"
            self._multiline_centered_text(draw, label, region.center, self.fonts["label"], self.style.text)

    def _draw_hud(self, draw: ImageDraw.ImageDraw, state: GameState) -> None:
        draw.rounded_rectangle((44, 34, 315, 122), radius=8, fill=(15, 12, 10, 190), outline=(212, 190, 138, 160))
        draw.text((70, 49), f"{state.current_year} AD", font=self.fonts["year"], fill=self.style.text)

        panel = (1445, 34, 1875, 330)
        draw.rounded_rectangle(panel, radius=8, fill=(15, 12, 10, 185), outline=(212, 190, 138, 145))
        draw.text((1475, 58), "势力排名", font=self.fonts["ranking"], fill=self.style.text)
        y = 104
        for rank, (faction_id, count) in enumerate(faction_ranking(state)[:6], start=1):
            faction = state.factions[faction_id]
            draw.rectangle((1477, y + 7, 1501, y + 31), fill=faction.color)
            name = faction.display_name(state.current_year)
            text = f"{rank}. {name}  {count}州"
            draw.text((1514, y), text, font=self.fonts["small"], fill=self.style.text)
            y += 36

    def _draw_subtitle(self, image: Image.Image, subtitle: str) -> None:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        bar_top = self.size[1] - 135
        draw.rectangle([(0, bar_top), (self.size[0], self.size[1])], fill=(12, 10, 8, 215))
        wrapped = self._wrap_text(draw, subtitle, self.fonts["subtitle"], self.size[0] - 260)
        line_height = 48
        total_height = line_height * len(wrapped)
        y = bar_top + (135 - total_height) / 2 - 4
        for line in wrapped:
            self._centered_text(draw, line, (self.size[0] // 2, int(y + line_height / 2)), self.fonts["subtitle"], self.style.subtitle_text)
            y += line_height
        image.alpha_composite(overlay)

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for candidate in self.style.font_candidates:
            path = Path(candidate)
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size=size)
                except OSError:
                    continue
        return ImageFont.load_default(size=size)

    def _wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = current + char
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines[:2] or [text]

    def _multiline_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        center: tuple[int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: str,
    ) -> None:
        lines = text.split("\n")
        line_height = 31
        start_y = center[1] - (len(lines) * line_height) / 2
        for index, line in enumerate(lines):
            self._centered_text(draw, line, (center[0], int(start_y + index * line_height + line_height / 2)), font, fill)

    def _centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        center: tuple[int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: str,
    ) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        shadow = (center[0] - width / 2 + 2, center[1] - height / 2 + 2)
        draw.text(shadow, text, font=font, fill=(0, 0, 0, 165))
        draw.text((center[0] - width / 2, center[1] - height / 2), text, font=font, fill=fill)


def render_result_frames(
    result: SimulationResult,
    map_config: MapConfig,
    style: StyleConfig,
    frames_dir: str | Path,
) -> int:
    frames_dir = clean_png_frames(frames_dir)
    renderer = FrameRenderer(map_config, style)
    total_frames = int(result.scenario.video_length_seconds * result.scenario.render_fps)
    year_to_state = {state.current_year: state for state in result.timeline}

    for frame_index in range(total_frames):
        seconds = frame_index / result.scenario.render_fps
        active_event = active_event_at(result.triggered_events, seconds)
        if active_event:
            year = active_event.year
            progress = (seconds - active_event.start_seconds) / max(0.1, active_event.duration_seconds)
        else:
            year = _year_for_seconds(result, seconds)
            progress = 0.0
        state = year_to_state.get(year) or _nearest_state(result.timeline, year)
        subtitle = subtitle_at(result.subtitles, seconds)
        frame = renderer.render_frame(state, active_event=active_event, subtitle=subtitle, event_progress=progress)
        frame.save(frames_dir / f"frame_{frame_index + 1:06d}.png")
    return total_frames


def active_event_at(events: list[TriggeredEvent], seconds: float) -> TriggeredEvent | None:
    active = [event for event in events if event.start_seconds <= seconds < event.end_seconds]
    if not active:
        return None
    return max(active, key=lambda event: event.importance)


def subtitle_at(subtitles: list[SubtitleEntry], seconds: float) -> str:
    for subtitle in subtitles:
        if subtitle.start_seconds <= seconds < subtitle.end_seconds:
            return subtitle.text
    return ""


def _year_for_seconds(result: SimulationResult, seconds: float) -> int:
    scenario = result.scenario
    progress = min(1.0, max(0.0, seconds / max(1, scenario.video_length_seconds)))
    return round(scenario.start_year + (scenario.end_year - scenario.start_year) * progress)


def _nearest_state(timeline: list[GameState], year: int) -> GameState:
    return min(timeline, key=lambda state: abs(state.current_year - year))


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _hex_to_rgba(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    return (*_hex_to_rgb(hex_color), alpha)
