from pathlib import Path

from hwsim.map.real_map import HistoricalOverlay, RealMapConfig, build_prepared_map, owner_for_province
from hwsim.utils.file_utils import read_json


def _tiny_geojson() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"shapeName": "Alpha Province"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"shapeName": "Beta Province"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]],
                },
            },
        ],
    }


def test_real_map_rasterization_assigns_owners() -> None:
    config = RealMapConfig(
        map_id="tiny",
        source_name="fixture",
        source_url="https://example.test",
        api_url="https://example.test/api",
        raw_geojson_file=Path("raw.geojson"),
        prepared_map_file=Path("prepared.json"),
        canvas_size=(100, 50),
        grid_size=(20, 10),
        margin=0,
        historical_overlay_file=Path("overlay.json"),
        attribution="fixture",
    )
    overlay = HistoricalOverlay(
        default_owner="cao",
        province_owner_rules=[{"contains": ["Beta"], "owner": "liu_bei"}],
    )

    prepared = build_prepared_map(_tiny_geojson(), config, overlay)

    assert prepared["land_cell_count"] > 0
    assert len(prepared["provinces"]) == 2
    assert prepared["provinces"][0]["owner"] == "cao"
    assert prepared["provinces"][1]["owner"] == "liu_bei"


def test_owner_for_province_uses_default() -> None:
    overlay = HistoricalOverlay(default_owner="cao", province_owner_rules=[])

    assert owner_for_province("Unknown Province", overlay) == "cao"


def test_default_overlay_only_targets_three_kingdoms() -> None:
    overlay = read_json("configs/maps/sanguo_historical_overlay.json")
    owners = {overlay["default_owner"]}
    owners.update(rule["owner"] for rule in overlay["province_owner_rules"])
    owners.update(overlay["spawn_province_rules"].keys())

    assert owners == {"cao", "liu_bei", "sun_quan"}
