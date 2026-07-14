"""Independent geometric checks for an irregular layout."""

from __future__ import annotations

from shapely import affinity


def verify_plan(plan: dict, config, expected_count: int) -> dict:
    errors = []
    count = sum(len(layer["placements"]) for layer in plan["layers"])
    if count != expected_count:
        errors.append(f"Expected {expected_count} placements, received {count}")
    minimum_clearance = None
    for layer in plan["layers"]:
        visible = []
        for placement in layer["placements"]:
            angle = placement["rotation_deg"] % 360
            allowed = placement.get("allowed_rotations_deg")
            if allowed:
                if not any(abs((angle - candidate) % 360) < 1e-6 for candidate in allowed):
                    errors.append(f"{placement['unit_id']} uses a disallowed rotation")
            elif abs((angle / config.rotation_step_deg) - round(angle / config.rotation_step_deg)) > 1e-6:
                errors.append(f"{placement['unit_id']} does not follow the rotation step")
            if placement.get("mirrored") and not config.allow_mirror:
                errors.append(f"{placement['unit_id']} was mirrored without permission")
            geometry = affinity.translate(
                placement["geometry"], config.edge_margin_cm, config.edge_margin_cm
            )
            min_x, min_y, max_x, max_y = geometry.bounds
            if min_x < config.edge_margin_cm - 1e-7 or min_y < config.edge_margin_cm - 1e-7:
                errors.append(f"{placement['unit_id']} is outside the lower carton margin")
            if max_x > plan["inner_width_cm"] - config.edge_margin_cm + 1e-7:
                errors.append(f"{placement['unit_id']} exceeds carton width")
            if max_y > plan["inner_height_cm"] - config.edge_margin_cm + 1e-7:
                errors.append(f"{placement['unit_id']} exceeds carton height")
            for other in visible:
                distance = geometry.distance(other)
                minimum_clearance = distance if minimum_clearance is None else min(minimum_clearance, distance)
                if distance + 1e-7 < config.item_clearance_cm:
                    errors.append(f"Clearance below target: {distance:.4f} cm")
            visible.append(geometry)
    return {
        "valid": not errors,
        "placement_count": count,
        "minimum_clearance_cm": round(minimum_clearance, 4) if minimum_clearance is not None else None,
        "errors": errors,
    }
