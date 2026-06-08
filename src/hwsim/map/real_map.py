from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from hwsim.utils.file_utils import ensure_dir, read_json, resolve_path, write_json


class RealMapConfig(BaseModel):
    map_id: str
    source_name: str
    source_url: str
    api_url: str
    download_field: str = "simplifiedGeometryGeoJSON"
    raw_geojson_file: Path
    prepared_map_file: Path
    canvas_size: tuple[int, int] = (1280, 720)
    grid_size: tuple[int, int] = (320, 180)
    margin: int = 24
    historical_overlay_file: Path
    attribution: str


class HistoricalOverlay(BaseModel):
    default_owner: str
    province_owner_rules: list[dict[str, Any]] = Field(default_factory=list)
    spawn_province_rules: dict[str, list[str]] = Field(default_factory=dict)


def load_real_map_config(path: str | Path) -> RealMapConfig:
    return RealMapConfig.model_validate(read_json(resolve_path(path)))


def prepare_real_map(config_path: str | Path, force: bool = False) -> Path:
    config = load_real_map_config(config_path)
    raw_path = resolve_path(config.raw_geojson_file)
    prepared_path = resolve_path(config.prepared_map_file)
    overlay = HistoricalOverlay.model_validate(read_json(resolve_path(config.historical_overlay_file)))

    if force or not raw_path.exists():
        _download_geoboundaries(config, raw_path)

    if force or not prepared_path.exists():
        geojson = read_json(raw_path)
        prepared = build_prepared_map(geojson, config, overlay)
        write_json(prepared_path, prepared)

    return prepared_path


def build_prepared_map(
    geojson: dict[str, Any],
    config: RealMapConfig,
    overlay: HistoricalOverlay,
) -> dict[str, Any]:
    features = geojson.get("features", [])
    if not features:
        raise ValueError("GeoJSON has no features")

    lon_lat_bounds = _bounds(features)
    projector = _Projector(lon_lat_bounds, config.canvas_size, config.margin)
    grid_w, grid_h = config.grid_size
    canvas_w, canvas_h = config.canvas_size
    grid_scale_x = grid_w / canvas_w
    grid_scale_y = grid_h / canvas_h

    province_grid = np.full((grid_h, grid_w), -1, dtype=np.int16)
    province_records: list[dict[str, Any]] = []

    for province_id, feature in enumerate(features):
        properties = feature.get("properties", {})
        name = _province_name(properties, province_id)
        mask = Image.new("L", (grid_w, grid_h), 0)
        draw = ImageDraw.Draw(mask)
        for polygon in _iter_polygons(feature.get("geometry", {})):
            if not polygon:
                continue
            exterior = [_scale_point(projector.project(lon, lat), grid_scale_x, grid_scale_y) for lon, lat in polygon[0]]
            if len(exterior) >= 3:
                draw.polygon(exterior, fill=1)
            for hole in polygon[1:]:
                hole_points = [_scale_point(projector.project(lon, lat), grid_scale_x, grid_scale_y) for lon, lat in hole]
                if len(hole_points) >= 3:
                    draw.polygon(hole_points, fill=0)

        mask_array = np.array(mask, dtype=bool)
        province_grid[mask_array] = province_id
        centroid_canvas = _feature_centroid(feature, projector)
        owner = owner_for_province(name, overlay)
        province_records.append(
            {
                "id": province_id,
                "name": name,
                "owner": owner,
                "centroid": [round(centroid_canvas[0], 2), round(centroid_canvas[1], 2)],
                "cell_count": int(mask_array.sum()),
            }
        )

    land_mask = province_grid >= 0
    if not land_mask.any():
        raise ValueError("Prepared real map has no land cells")

    return {
        "map_id": config.map_id,
        "source_name": config.source_name,
        "source_url": config.source_url,
        "attribution": config.attribution,
        "canvas_size": list(config.canvas_size),
        "grid_size": list(config.grid_size),
        "bounds": {
            "min_lon": lon_lat_bounds[0],
            "min_lat": lon_lat_bounds[1],
            "max_lon": lon_lat_bounds[2],
            "max_lat": lon_lat_bounds[3],
        },
        "provinces": province_records,
        "province_id_grid": province_grid.tolist(),
        "land_cell_count": int(land_mask.sum()),
        "spawn_province_rules": overlay.spawn_province_rules,
    }


def owner_for_province(name: str, overlay: HistoricalOverlay) -> str:
    lower_name = name.lower()
    for rule in overlay.province_owner_rules:
        owner = rule.get("owner")
        matches = [str(item).lower() for item in rule.get("contains", [])]
        if owner and any(match in lower_name for match in matches):
            return str(owner)
    return overlay.default_owner


def _download_geoboundaries(config: RealMapConfig, raw_path: Path) -> None:
    ensure_dir(raw_path.parent)
    with urllib.request.urlopen(config.api_url, timeout=30) as response:
        metadata = json.load(response)
    download_url = metadata.get(config.download_field)
    if not download_url:
        raise ValueError(f"geoBoundaries metadata missing {config.download_field}")
    with urllib.request.urlopen(download_url, timeout=60) as response:
        raw_path.write_bytes(response.read())


def _province_name(properties: dict[str, Any], fallback_id: int) -> str:
    for key in ("shapeName", "shapeName_en", "name", "NAME_1", "admin1Name"):
        value = properties.get(key)
        if value:
            return str(value)
    return f"province_{fallback_id}"


def _iter_polygons(geometry: dict[str, Any]) -> list[list[list[tuple[float, float]]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        return [_normalize_polygon(coordinates)]
    if geometry_type == "MultiPolygon":
        return [_normalize_polygon(polygon) for polygon in coordinates]
    return []


def _normalize_polygon(raw_polygon: list[Any]) -> list[list[tuple[float, float]]]:
    return [[(float(point[0]), float(point[1])) for point in ring] for ring in raw_polygon]


def _bounds(features: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    coords: list[tuple[float, float]] = []
    for feature in features:
        for polygon in _iter_polygons(feature.get("geometry", {})):
            for ring in polygon:
                coords.extend(ring)
    if not coords:
        raise ValueError("GeoJSON has no polygon coordinates")
    lons = [lon for lon, _lat in coords]
    lats = [lat for _lon, lat in coords]
    return min(lons), min(lats), max(lons), max(lats)


def _feature_centroid(feature: dict[str, Any], projector: _Projector) -> tuple[float, float]:
    points: list[tuple[float, float]] = []
    for polygon in _iter_polygons(feature.get("geometry", {})):
        if polygon:
            points.extend(projector.project(lon, lat) for lon, lat in polygon[0])
    if not points:
        return 0.0, 0.0
    return sum(x for x, _y in points) / len(points), sum(y for _x, y in points) / len(points)


def _scale_point(point: tuple[float, float], scale_x: float, scale_y: float) -> tuple[int, int]:
    return int(round(point[0] * scale_x)), int(round(point[1] * scale_y))


class _Projector:
    def __init__(self, bounds: tuple[float, float, float, float], canvas_size: tuple[int, int], margin: int) -> None:
        min_lon, min_lat, max_lon, max_lat = bounds
        width, height = canvas_size
        lon_span = max(0.0001, max_lon - min_lon)
        lat_span = max(0.0001, max_lat - min_lat)
        self.min_lon = min_lon
        self.max_lat = max_lat
        self.scale = min((width - margin * 2) / lon_span, (height - margin * 2) / lat_span)
        projected_width = lon_span * self.scale
        projected_height = lat_span * self.scale
        self.offset_x = (width - projected_width) / 2
        self.offset_y = (height - projected_height) / 2

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        return (
            self.offset_x + (lon - self.min_lon) * self.scale,
            self.offset_y + (self.max_lat - lat) * self.scale,
        )

