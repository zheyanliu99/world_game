from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from hwsim.core.models import StyleConfig, SubtitleEntry
from hwsim.physics.models import MarbleGameState


class MarbleRenderer:
    def __init__(self, style: StyleConfig) -> None:
        self.style = style
        self._capital_cache: dict[tuple[int, int], dict[str, tuple[float, float]]] = {}
        self.fonts = {
            "year": self._font(42),
            "title": self._font(34),
            "small": self._font(20),
            "capital": self._font(24),
            "subtitle": self._font(27),
            "event_title": self._font(38),
            "event_subtitle": self._font(24),
        }

    def render(self, state: MarbleGameState, subtitle: str = "", speed_label: str = "") -> Image.Image:
        image = self._territory_image(state).convert("RGBA")
        draw = ImageDraw.Draw(image)
        self._draw_marbles(draw, state)
        self._draw_capital_labels(draw, state)
        self._draw_hud(draw, state, speed_label)
        if state.active_event:
            self._draw_event(draw, state)
        if subtitle:
            self._draw_subtitle(image, subtitle)
        return image.convert("RGB")

    def _territory_image(self, state: MarbleGameState) -> Image.Image:
        owner_grid = state.grid.owner_grid
        grid_h, grid_w = owner_grid.shape
        rgb = np.zeros((grid_h, grid_w, 3), dtype=np.uint8)
        rgb[:, :, :] = _hex_to_rgb(self.style.background)
        for owner_index, faction_id in enumerate(state.grid.faction_ids):
            faction = state.factions[faction_id]
            color = np.array(_hex_to_rgb(faction.color), dtype=np.uint8)
            rgb[owner_grid == owner_index] = (color * 0.82).astype(np.uint8)
        edge = _edge_mask(owner_grid, state.grid.land_mask)
        border = np.array(_hex_to_rgb(self.style.border), dtype=np.float32)
        rgb[edge] = np.clip(rgb[edge].astype(np.float32) * 0.62 + border * 0.38, 0, 255).astype(np.uint8)
        image = Image.fromarray(rgb, mode="RGB")
        return image.resize(state.grid.canvas_size, Image.Resampling.NEAREST)

    def _draw_marbles(self, draw: ImageDraw.ImageDraw, state: MarbleGameState) -> None:
        for marble in state.marbles:
            faction = state.factions[marble.faction_id]
            radius = marble.radius * (1.0 + max(0, marble.power - 1.0) * 0.18)
            box = (marble.x - radius, marble.y - radius, marble.x + radius, marble.y + radius)
            draw.ellipse(box, fill=faction.color, outline="#FFF3CC", width=1)

    def _draw_capital_labels(self, draw: ImageDraw.ImageDraw, state: MarbleGameState) -> None:
        for faction_id, (x, y) in self._capital_label_positions(state).items():
            faction = state.factions[faction_id]
            label = faction.display_name(state.current_year)
            font = self.fonts["capital"]
            bbox = draw.textbbox((0, 0), label, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            label_x = x - text_w / 2
            label_y = y - text_h - 16
            panel = (
                label_x - 7,
                label_y - 4,
                label_x + text_w + 7,
                label_y + text_h + 5,
            )
            draw.rounded_rectangle(panel, radius=5, fill=(9, 8, 6, 205), outline=faction.color, width=1)
            draw.text((label_x + 1, label_y + 1), label, font=font, fill=(0, 0, 0, 160))
            draw.text((label_x, label_y), label, font=font, fill=self.style.text)
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=faction.color, outline="#FFF3CC", width=1)

    def _capital_label_positions(self, state: MarbleGameState) -> dict[str, tuple[float, float]]:
        cache_key = (id(state.grid.owner_grid), state.current_year)
        cached = self._capital_cache.get(cache_key)
        if cached is not None:
            return cached

        positions: dict[str, tuple[float, float]] = {}
        for faction_id in state.factions:
            centroid = self._largest_owned_component_centroid(state, faction_id)
            if centroid is not None:
                positions[faction_id] = centroid
        self._capital_cache = {cache_key: positions}
        return positions

    def _largest_owned_component_centroid(
        self,
        state: MarbleGameState,
        faction_id: str,
    ) -> tuple[float, float] | None:
        owner_index = state.grid.faction_index(faction_id)
        owner_grid = state.grid.owner_grid
        mask = owner_grid == owner_index
        if not bool(mask.any()):
            return None

        height, width = owner_grid.shape
        visited = np.zeros(owner_grid.shape, dtype=bool)
        best_count = 0
        best_sum_x = 0
        best_sum_y = 0
        start_ys, start_xs = np.where(mask)
        for start_y, start_x in zip(start_ys, start_xs, strict=True):
            if visited[start_y, start_x]:
                continue
            queue: deque[tuple[int, int]] = deque([(int(start_x), int(start_y))])
            visited[start_y, start_x] = True
            count = 0
            sum_x = 0
            sum_y = 0
            while queue:
                x, y = queue.popleft()
                count += 1
                sum_x += x
                sum_y += y
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if visited[ny, nx] or not mask[ny, nx]:
                        continue
                    visited[ny, nx] = True
                    queue.append((nx, ny))
            if count > best_count:
                best_count = count
                best_sum_x = sum_x
                best_sum_y = sum_y

        if best_count == 0:
            return None
        cell_w, cell_h = state.grid.cell_size
        return (
            (best_sum_x / best_count + 0.5) * cell_w,
            (best_sum_y / best_count + 0.5) * cell_h,
        )

    def _draw_hud(self, draw: ImageDraw.ImageDraw, state: MarbleGameState, speed_label: str) -> None:
        width, _height = state.grid.canvas_size
        draw.rounded_rectangle((24, 22, 238, 92), radius=8, fill=(12, 10, 8, 210), outline=(218, 196, 138, 175), width=2)
        draw.text((46, 36), f"{state.current_year} AD", font=self.fonts["year"], fill=self.style.text)

        if speed_label:
            draw.rounded_rectangle((254, 32, 375, 82), radius=8, fill=(12, 10, 8, 190), outline=(218, 196, 138, 120), width=1)
            draw.text((274, 43), speed_label, font=self.fonts["small"], fill=self.style.text)

        panel = (width - 290, 22, width - 24, 288)
        draw.rounded_rectangle(panel, radius=8, fill=(12, 10, 8, 205), outline=(218, 196, 138, 150), width=2)
        draw.text((panel[0] + 22, panel[1] + 18), "格子排名", font=self.fonts["title"], fill=self.style.text)
        counts = state.grid.owned_cell_counts()
        ranking = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:3]
        y = panel[1] + 66
        for index, (faction_id, count) in enumerate(ranking, start=1):
            faction = state.factions[faction_id]
            draw.rectangle((panel[0] + 24, y + 6, panel[0] + 44, y + 26), fill=faction.color)
            units = sum(1 for marble in state.marbles if marble.faction_id == faction_id)
            text = f"{index}. {faction.display_name(state.current_year)} {count}格/{units}球"
            draw.text((panel[0] + 55, y), text, font=self.fonts["small"], fill=self.style.text)
            y += 31

        if state.grid.attribution:
            draw.text((24, state.grid.canvas_size[1] - 26), state.grid.attribution, font=self.fonts["small"], fill=self.style.muted_text)

    def _draw_event(self, draw: ImageDraw.ImageDraw, state: MarbleGameState) -> None:
        event = state.active_event
        if event is None:
            return
        width, height = state.grid.canvas_size
        if event.effect == "collapse_flash":
            draw.rectangle((0, 0, width, 8), fill=(180, 45, 36))
            draw.rectangle((0, height - 8, width, height), fill=(180, 45, 36))
        elif event.effect == "fire_overlay":
            pass
        elif event.effect == "edict_overlay":
            draw.rounded_rectangle((width // 2 - 260, 144, width // 2 + 260, 236), radius=8, fill=(116, 86, 36), outline=(255, 220, 130), width=2)
        elif event.effect == "marching_arrows":
            for offset in range(0, 240, 60):
                x = 210 + offset
                draw.line((x, 160, x + 48, 132), fill=(255, 220, 120, 160), width=6)
                draw.polygon([(x + 48, 132), (x + 31, 130), (x + 40, 147)], fill=(255, 220, 120, 160))
        elif event.effect == "betrayal_flash":
            pass

        banner = (width // 2 - 360, 42, width // 2 + 360, 136)
        draw.rounded_rectangle(banner, radius=8, fill=(10, 8, 6, 225), outline=(238, 204, 130, 180), width=2)
        self._centered_text(draw, event.title, (width // 2, 72), self.fonts["event_title"], self.style.event_title)
        self._centered_text(draw, event.subtitle, (width // 2, 112), self.fonts["event_subtitle"], self.style.event_subtitle)

    def _draw_subtitle(self, image: Image.Image, subtitle: str) -> None:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        width, height = image.size
        draw.rectangle((0, height - 86, width, height), fill=(10, 8, 6, 220))
        lines = self._wrap_text(draw, subtitle, self.fonts["subtitle"], width - 150)
        y = height - 66 if len(lines) == 1 else height - 76
        for line in lines[:2]:
            self._centered_text(draw, line, (width // 2, y), self.fonts["subtitle"], self.style.subtitle_text)
            y += 34
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
        return lines or [text]

    def _centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        center: tuple[int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: str,
    ) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = center[0] - text_width / 2
        y = center[1] - text_height / 2
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 150))
        draw.text((x, y), text, font=font, fill=fill)


def _edge_mask(owner_grid: np.ndarray, land_mask: np.ndarray) -> np.ndarray:
    edge = np.zeros(owner_grid.shape, dtype=bool)
    edge[:, 1:] |= owner_grid[:, 1:] != owner_grid[:, :-1]
    edge[1:, :] |= owner_grid[1:, :] != owner_grid[:-1, :]
    return edge & land_mask


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def marble_subtitles(state: MarbleGameState) -> list[SubtitleEntry]:
    intro_end = min(3.0, state.scenario.video_length_seconds)
    subtitles = [
        SubtitleEntry(
            start_seconds=0,
            end_seconds=intro_end,
            text=state.scenario.intro_narration,
        )
    ]
    cursor = intro_end
    for event in sorted(state.triggered_events, key=lambda item: item.start_seconds):
        start = max(event.start_seconds, cursor + 0.08)
        if start >= state.scenario.video_length_seconds:
            break
        end = min(state.scenario.video_length_seconds, start + min(2.7, event.duration_seconds))
        if end <= start:
            continue
        subtitles.append(
            SubtitleEntry(
                start_seconds=start,
                end_seconds=end,
                text=event.narration,
            )
        )
        cursor = end
    return subtitles
