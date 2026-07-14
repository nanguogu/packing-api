"""Cost orchestration for irregular layered packing plans."""

from __future__ import annotations

import math

from shapely import affinity

from app.schemas import QuotePackage, ShippingQuoteRequest
from app.services.public_shipping import QuoteUnavailable, quote_public_shipment

from .geometry import geometry_to_polygons
from .layer_planner import plan_layers
from .nesting_engine import expand_instances
from .verifier import verify_plan


def _round_dimensions(plan: dict, wall: float) -> dict:
    inner = {
        "length_cm": math.ceil(plan["inner_width_cm"] * 10) / 10,
        "width_cm": math.ceil(plan["inner_height_cm"] * 10) / 10,
        "height_cm": math.ceil(plan["inner_depth_cm"] * 10) / 10,
    }
    outer = {key: round(value + 2 * wall, 1) for key, value in inner.items()}
    return {"inner": inner, "outer": outer}


def _serialize_plan(plan: dict, config) -> dict:
    layers = []
    for layer in plan["layers"]:
        placements = []
        for item in layer["placements"]:
            display_geometry = affinity.translate(
                item["geometry"], config.edge_margin_cm, config.edge_margin_cm
            )
            min_x, min_y, _, _ = display_geometry.bounds
            placements.append({
                "unit_id": item["unit_id"], "name": item["name"],
                "instance": item["instance"], "layer": layer["layer"],
                "x_cm": round(min_x, 4),
                "y_cm": round(min_y, 4),
                "rotation_deg": round(item["rotation_deg"], 4),
                "mirrored": bool(item.get("mirrored")),
                "polygons": geometry_to_polygons(display_geometry),
            })
        layers.append({
            "layer": layer["layer"],
            "used_width_cm": round(layer["used_width_cm"], 4),
            "used_height_cm": round(layer["used_height_cm"], 4),
            "placements": placements,
        })
    return layers


def optimize_irregular_order(request) -> dict:
    """Enumerate safe layer counts and select the lowest priced SG-rate plan."""
    instances = expand_instances(request.units)
    candidates = plan_layers(instances, request.packing)
    actual_weight = sum(item["weight_kg"] for item in instances) + request.packaging_weight_kg
    provided_costs = {
        "packaging": request.packaging_cost_hkd,
        "labor": request.labor_cost_hkd,
        "material": request.material_cost_hkd,
        "risk": request.risk_cost_hkd,
    }
    additional = sum(value or 0 for value in provided_costs.values())
    cost_scope = "full_configured_total_cost" if all(value is not None for value in provided_costs.values()) else (
        "shipping_plus_provided_costs" if any(value is not None for value in provided_costs.values()) else "shipping_only"
    )

    evaluated = []
    for plan in candidates:
        verification = verify_plan(plan, request.packing, len(instances))
        if not verification["valid"]:
            continue
        dimensions = _round_dimensions(plan, request.packing.wall_allowance_cm)
        outer = dimensions["outer"]
        quote_request = ShippingQuoteRequest(
            origin="HK", destination=request.pricing_destination,
            service_type=request.service_type,
            packages=[QuotePackage(
                reference=f"{request.order_id}-1",
                length_cm=outer["length_cm"], width_cm=outer["width_cm"],
                height_cm=outer["height_cm"], weight_kg=actual_weight,
            )],
        )
        try:
            shipping = quote_public_shipment(quote_request)
        except QuoteUnavailable:
            continue
        shipping_total = shipping["recommended"]["total"]
        total = round(shipping_total + additional, 2)
        area = plan["inner_width_cm"] * plan["inner_height_cm"]
        evaluated.append({
            "sort_key": (total, plan["layer_count"], area),
            "plan": plan, "dimensions": dimensions, "verification": verification,
            "shipping": shipping, "shipping_total_hkd": shipping_total, "total_cost_hkd": total,
        })
    if not evaluated:
        raise QuoteUnavailable("No valid irregular packing candidate can be priced by the SG public rate card")
    evaluated.sort(key=lambda item: item["sort_key"])
    selected = evaluated[0]
    plan = selected["plan"]
    total_piece_area = sum(item["area"] for item in instances)
    capacity_area = plan["inner_width_cm"] * plan["inner_height_cm"] * plan["layer_count"]
    engines = [layer["engine"] for layer in plan["layers"]]
    lower_bound = total_piece_area / plan["layer_count"]
    return {
        "status": "best_found",
        "order_id": request.order_id,
        "objective": request.objective,
        "requested_destination": request.requested_destination.upper(),
        "pricing_destination": request.pricing_destination,
        "pricing_lane_used": "HK-SG",
        "pricing_notice": "All current cost comparisons use the configured Hong Kong to Singapore public rate card.",
        "cost_scope": cost_scope,
        "costs_hkd": {
            "shipping": selected["shipping_total_hkd"], **provided_costs,
            "configured_additional_total": round(additional, 2),
            "selected_total": selected["total_cost_hkd"],
        },
        "carton": {
            "packaging_type": request.packaging_type,
            "inner_dimensions_cm": selected["dimensions"]["inner"],
            "outer_dimensions_cm": selected["dimensions"]["outer"],
            "actual_weight_kg": round(actual_weight, 3),
            "layer_count": plan["layer_count"],
        },
        "utilization": round(total_piece_area / capacity_area, 4),
        "piece_area_cm2": round(total_piece_area, 4),
        "lower_bound_footprint_area_cm2": round(lower_bound, 4),
        "best_footprint_area_cm2": round(plan["inner_width_cm"] * plan["inner_height_cm"], 4),
        "quality_ratio": round((plan["inner_width_cm"] * plan["inner_height_cm"]) / lower_bound, 4),
        "layers": _serialize_plan(plan, request.packing),
        "verification": selected["verification"],
        "solver": {
            "name": engines[0]["name"],
            "production_target": engines[0].get("production_target", "u-nesting"),
            "fallback": any(engine.get("fallback") for engine in engines),
            "requested_seed": request.packing.seed,
            "seed_applied": engines[0]["name"] == "python_baseline",
            "elapsed_ms": round(sum(engine["elapsed_ms"] for engine in engines), 2),
            "note": "python_baseline is a deterministic development fallback, not U-Nesting" if any(engine.get("fallback") for engine in engines) else None,
        },
        "shipping": selected["shipping"],
        "alternatives": [
            {
                "rank": index + 2,
                "layer_count": value["plan"]["layer_count"],
                "outer_dimensions_cm": value["dimensions"]["outer"],
                "shipping_total_hkd": value["shipping_total_hkd"],
                "total_cost_hkd": value["total_cost_hkd"],
            }
            for index, value in enumerate(evaluated[1:4])
        ],
    }
