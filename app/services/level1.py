"""Level 1 MVP orchestration: one order, three cuboids, one custom carton."""

from __future__ import annotations

from app.core.engine import calculate_min_box
from app.schemas import Level1PackingRequest, QuotePackage, ShippingQuoteRequest
from app.services.public_shipping import quote_public_shipment


def optimize_level1_order(request: Level1PackingRequest) -> dict:
    """Find the minimum-volume single carton and compare public shipping rates."""
    items = [item.to_dict() for item in request.items]
    packing = calculate_min_box(
        items,
        time_limit_s=request.time_limit_s,
        objective="volume",
    )
    if not packing or not packing.get("success"):
        raise ValueError("No feasible single-carton layout was found")

    box = packing["box"]
    total_weight = round(sum(item.weight_kg for item in request.items), 3)
    quote_request = ShippingQuoteRequest(
        origin=request.origin,
        destination=request.destination,
        service_type=request.service_type,
        packages=[QuotePackage(
            reference=f"{request.order_id}-CARTON-1",
            length_cm=box["length_cm"],
            width_cm=box["width_cm"],
            height_cm=box["height_cm"],
            weight_kg=total_weight,
        )],
    )
    shipping = quote_public_shipment(quote_request)
    carton_volume = box["length_cm"] * box["width_cm"] * box["height_cm"]

    return {
        "level": 1,
        "order_id": request.order_id,
        "strategy": "single_custom_carton",
        "destination": {
            "country_code": request.destination.upper(),
            "address": request.destination_address,
        },
        "service_type": request.service_type,
        "packing": {
            "carton_count": 1,
            "carton": {
                "reference": f"{request.order_id}-CARTON-1",
                "dimensions_cm": box,
                "volume_cm3": carton_volume,
                "actual_weight_kg": total_weight,
            },
            "layout": packing["layout"],
            "utilization": packing["utilization"],
            "solve_time_ms": packing["solve_time_ms"],
            "solver_status": packing["status"],
            "objective": "minimum_carton_volume",
        },
        "shipping": shipping,
        "recommendation": {
            "carton_strategy": "single_custom_carton",
            "carrier": shipping["recommended"]["carrier"],
            "currency": shipping["currency"],
            "shipping_total": shipping["recommended"]["total"],
        },
        "scope_notices": [
            "Level 1 compares one minimum-volume custom carton only.",
            "The one-carton quote includes currently configured physical surcharges, but does not rearrange or split cartons to avoid them.",
            "Padding, carton manufacturing cost, and split-carton alternatives are deferred to Level 2.",
        ],
    }
