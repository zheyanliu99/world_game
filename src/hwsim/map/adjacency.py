from __future__ import annotations

from hwsim.core.models import Region


def validate_symmetric_adjacency(regions: dict[str, Region]) -> list[str]:
    errors: list[str] = []
    for region_id, region in regions.items():
        for neighbor_id in region.neighbors:
            neighbor = regions.get(neighbor_id)
            if neighbor is None:
                errors.append(f"{region_id} references missing neighbor {neighbor_id}")
            elif region_id not in neighbor.neighbors:
                errors.append(f"{region_id} -> {neighbor_id} is not symmetric")
    return errors

