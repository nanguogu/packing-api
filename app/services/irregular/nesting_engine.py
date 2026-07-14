"""U-Nesting process adapter with an explicit deterministic development fallback."""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import time

from shapely import affinity

from .geometry import geometry_to_polygons, polygons_to_geometry


def _expanded_instances(units):
    values = []
    for unit in units:
        source = polygons_to_geometry(unit.polygons)
        min_x, min_y, _, _ = source.bounds
        source = affinity.translate(source, -min_x, -min_y)
        for instance in range(1, unit.quantity + 1):
            values.append({
                "unit_id": unit.unit_id,
                "name": unit.name,
                "instance": instance,
                "geometry": source,
                "area": source.area,
                "allowed_rotations_deg": unit.allowed_rotations_deg,
                "thickness_cm": unit.thickness_cm,
                "weight_kg": unit.weight_kg,
                "stackable": unit.stackable,
            })
    return values


def _angles(item, step: float) -> list[float]:
    if item["allowed_rotations_deg"]:
        return sorted({float(value) % 360 for value in item["allowed_rotations_deg"]})
    count = max(1, math.ceil(360 / step))
    return [round(index * step, 6) for index in range(count)]


def _place_layer(items, width_limit: float, clearance: float, step: float):
    placed = []
    for item in sorted(items, key=lambda value: value["area"], reverse=True):
        candidates = {(0.0, 0.0)}
        for current in placed:
            _, _, right, top = current["collision_geometry"].bounds
            candidates.add((right, 0.0))
            candidates.add((0.0, top))
            for other in placed:
                candidates.add((right, other["collision_geometry"].bounds[3]))
        best = None
        for angle in _angles(item, step):
            rotated = affinity.rotate(item["geometry"], angle, origin=(0, 0))
            min_x, min_y, _, _ = rotated.bounds
            rotated = affinity.translate(rotated, -min_x, -min_y)
            collision = rotated.buffer(clearance / 2, join_style=2)
            collision_min_x, collision_min_y, _, _ = collision.bounds
            for x, y in candidates:
                dx, dy = x - collision_min_x, y - collision_min_y
                candidate = affinity.translate(collision, dx, dy)
                if candidate.bounds[2] > width_limit + 1e-8:
                    continue
                if any(candidate.intersection(current["collision_geometry"]).area > 1e-8 for current in placed):
                    continue
                height = max([candidate.bounds[3], *[p["collision_geometry"].bounds[3] for p in placed]])
                score = (height, x, y, angle)
                if best is None or score < best[0]:
                    visible = affinity.translate(rotated, dx, dy)
                    best = (score, angle, visible, candidate)
        if best is None:
            return None
        _, angle, visible, collision = best
        placed.append({
            **item, "rotation_deg": angle, "mirrored": False,
            "geometry": visible, "collision_geometry": collision,
        })
    return placed


def _python_baseline(items, config):
    max_piece = max(max(item["geometry"].bounds[2:]) for item in items)
    total_area = sum(item["geometry"].buffer(config.item_clearance_cm / 2).area for item in items)
    sum_width = sum(item["geometry"].bounds[2] + config.item_clearance_cm for item in items)
    lower_width = max(max_piece + config.item_clearance_cm, math.sqrt(total_area) * 0.7)
    available_width = (
        config.max_inner_width_cm - 2 * config.edge_margin_cm
        if config.max_inner_width_cm else None
    )
    upper_width = available_width or max(lower_width, sum_width)
    if upper_width < lower_width:
        raise ValueError("Configured maximum inner width is smaller than a required piece")
    widths = {lower_width, upper_width}
    for index in range(1, 13):
        widths.add(lower_width + (upper_width - lower_width) * index / 13)

    best = None
    for width in sorted(widths):
        placed = _place_layer(items, width, config.item_clearance_cm, config.rotation_step_deg)
        if not placed:
            continue
        max_x = max(item["collision_geometry"].bounds[2] for item in placed)
        max_y = max(item["collision_geometry"].bounds[3] for item in placed)
        if config.max_inner_height_cm and max_y > config.max_inner_height_cm - 2 * config.edge_margin_cm:
            continue
        score = (max_x * max_y, max(max_x, max_y), max_x + max_y)
        if best is None or score < best[0]:
            best = (score, placed, max_x, max_y)
    if best is None:
        raise ValueError("No feasible 2D layout was found within the configured dimensions")
    _, placed, used_width, used_height = best
    return {
        "engine": {"name": "python_baseline", "production_target": "u-nesting", "fallback": True},
        "used_width_cm": used_width,
        "used_height_cm": used_height,
        "placements": placed,
    }


def _external_u_nesting(items, config, command: str):
    geometries = []
    lookup = {}
    for item in items:
        polygons = geometry_to_polygons(item["geometry"])
        if len(polygons) != 1:
            raise ValueError("U-Nesting FFI currently requires one connected polygon per packing unit")
        identifier = f"{item['unit_id']}::{item['instance']}"
        lookup[identifier] = item
        geometries.append({
            "id": identifier,
            "polygon": polygons[0]["outer"],
            "holes": polygons[0]["holes"] if config.part_in_part else [],
            "quantity": 1,
            "rotations": _angles(item, config.rotation_step_deg),
            "allow_flip": config.allow_mirror,
        })
    boundary_width = (config.max_inner_width_cm - 2 * config.edge_margin_cm) if config.max_inner_width_cm else sum(
        item["geometry"].bounds[2] + config.item_clearance_cm for item in items
    )
    boundary_height = (config.max_inner_height_cm - 2 * config.edge_margin_cm) if config.max_inner_height_cm else sum(
        item["geometry"].bounds[3] + config.item_clearance_cm for item in items
    )
    if boundary_width <= 0 or boundary_height <= 0:
        raise ValueError("Configured inner dimensions must exceed twice the edge margin")
    payload = {
        "mode": "2d", "version": "1.0", "geometries": geometries,
        "boundary": {"width": boundary_width, "height": boundary_height},
        "config": {
            "strategy": "nfp", "spacing": config.item_clearance_cm,
            "margin": 0, "time_limit_ms": int(config.time_limit_s * 1000),
            "multi_sheet": False,
        },
    }
    completed = subprocess.run(
        shlex.split(command), input=json.dumps(payload), text=True, capture_output=True,
        timeout=config.time_limit_s + 5, check=True,
    )
    result = json.loads(completed.stdout)
    if not result.get("success") or result.get("unplaced"):
        raise ValueError(f"U-Nesting did not place every item: {result.get('unplaced', [])}")
    placements = []
    for value in result["placements"]:
        item = lookup[value["id"]]
        geometry = affinity.rotate(item["geometry"], value["rotation"], origin=(0, 0))
        if value.get("flipped"):
            geometry = affinity.scale(geometry, xfact=-1, yfact=1, origin=(0, 0))
        min_x, min_y, _, _ = geometry.bounds
        geometry = affinity.translate(geometry, value["x"] - min_x, value["y"] - min_y)
        placements.append({
            **item, "rotation_deg": value["rotation"], "mirrored": bool(value.get("flipped")),
            "geometry": geometry,
            "collision_geometry": geometry.buffer(config.item_clearance_cm / 2, join_style=2),
        })
    used_width = max(item["collision_geometry"].bounds[2] for item in placements)
    used_height = max(item["collision_geometry"].bounds[3] for item in placements)
    return {
        "engine": {"name": "u-nesting", "version": result.get("version"), "fallback": False},
        "used_width_cm": used_width,
        "used_height_cm": used_height,
        "placements": placements,
    }


def nest_layer(items, config):
    started = time.perf_counter()
    command = os.getenv("U_NESTING_COMMAND")
    result = _external_u_nesting(items, config, command) if command else _python_baseline(items, config)
    result["engine"]["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["engine"]["seed"] = config.seed
    return result


def expand_instances(units):
    return _expanded_instances(units)
