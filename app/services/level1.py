"""Level 1 MVP orchestration: one order, three cuboids, one custom carton."""

from __future__ import annotations

from html import escape

from app.core.engine import calculate_min_box
from app.schemas import Level1PackingRequest, QuotePackage, ShippingQuoteRequest
from app.services.public_shipping import quote_public_shipment
from app.services.viz import generate_3d_html


def _placement_order(layout: list[dict]) -> list[dict]:
    """Return a stable bottom-up, large-before-small physical placement order."""
    ordered = sorted(
        layout,
        key=lambda item: (
            item["position"]["z"],
            -item["placed_dims"]["length"]
            * item["placed_dims"]["width"]
            * item["placed_dims"]["height"],
            item["sku"],
        ),
    )
    return [{**item, "step": index} for index, item in enumerate(ordered, start=1)]


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

    ordered_layout = _placement_order(packing["layout"])
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
            "layout": ordered_layout,
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


def generate_level1_guide_html(result: dict) -> str:
    """Render an item-by-item 3D work instruction from a Level 1 result."""
    carton = result["packing"]["carton"]
    dimensions = carton["dimensions_cm"]
    recommendation = result["recommendation"]
    instruction_items = []
    for item in result["packing"]["layout"]:
        position = item["position"]
        size = item["placed_dims"]
        instruction_items.append(
            "<li>第 {step} 步：放置 <b>{sku}</b>；起点坐标 "
            "({x:.1f}, {y:.1f}, {z:.1f}) cm；旋转后尺寸 "
            "{length:.1f}×{width:.1f}×{height:.1f} cm；方向代码 {rotation}。</li>".format(
                step=item["step"],
                sku=escape(item["sku"]),
                x=position["x"], y=position["y"], z=position["z"],
                length=size["length"], width=size["width"], height=size["height"],
                rotation=escape(item["rotation"]),
            )
        )
    guide_html = (
        f"<h1>第 1 层级 3D 装箱指南 — {escape(result['order_id'])}</h1>"
        f"<p>定制纸箱：<b>{dimensions['length_cm']}×{dimensions['width_cm']}×{dimensions['height_cm']} cm</b>；"
        f"空间利用率：<b>{result['packing']['utilization']:.2%}</b>。</p>"
        f"<p>推荐物流：<b>{escape(recommendation['carrier'])}</b>；"
        f"费用：{escape(recommendation['currency'])} <b>{recommendation['shipping_total']:,.2f}</b>。</p>"
        f"<ol>{''.join(instruction_items)}</ol>"
    )
    packer_result = {
        "groups": [{
            "box": dimensions,
            "layout": result["packing"]["layout"],
        }]
    }
    return generate_3d_html(
        packer_result,
        step_mode="item",
        page_title="第 1 层级 3D 装箱指南",
        guide_html=guide_html,
    )
