"""Level 2 MVP: cost-optimize all partitions of one to five cuboids."""

from __future__ import annotations

from html import escape
from itertools import product

from app.core.engine import calculate_min_box
from app.schemas import Level2PackingRequest, QuotePackage, ShippingQuoteRequest
from app.services.level1 import _ROTATION_DESCRIPTIONS, _placement_order
from app.services.public_shipping import QuoteUnavailable, quote_public_shipment
from app.services.viz import generate_3d_html


def _partitions(values: tuple[int, ...]):
    """Yield every set partition exactly once in canonical order."""
    if not values:
        yield ()
        return
    first, *rest = values
    for partition in _partitions(tuple(rest)):
        yield ((first,),) + partition
        for index in range(len(partition)):
            yield partition[:index] + ((first,) + partition[index],) + partition[index + 1:]


def _candidate_boxes(items: list[dict], time_limit_s: float) -> list[dict]:
    candidates = []
    seen = set()
    for objective in ("volume", "edge_sum"):
        result = calculate_min_box(
            items,
            time_limit_s=time_limit_s,
            objective=objective,
            require_support=True,
        )
        if not result or not result.get("success"):
            continue
        dims = result["box"]
        key = tuple(sorted(dims.values()))
        if key in seen:
            continue
        seen.add(key)
        result["candidate_objective"] = objective
        candidates.append(result)
    return candidates


def _quote(request: Level2PackingRequest, cartons: list[dict]) -> dict:
    packages = [QuotePackage(
        reference=carton["reference"],
        length_cm=carton["dimensions_cm"]["length_cm"],
        width_cm=carton["dimensions_cm"]["width_cm"],
        height_cm=carton["dimensions_cm"]["height_cm"],
        weight_kg=carton["actual_weight_kg"],
    ) for carton in cartons]
    return quote_public_shipment(ShippingQuoteRequest(
        origin=request.origin,
        destination=request.destination,
        service_type=request.service_type,
        packages=packages,
    ))


def optimize_level2_order(request: Level2PackingRequest) -> dict:
    """Compare every item partition, candidate carton shape, and carrier."""
    item_dicts = [item.to_dict() for item in request.items]
    item_count = len(item_dicts)
    subset_cache: dict[tuple[int, ...], list[dict]] = {}
    evaluated = 0
    ranked_plans = []
    partitions = list(_partitions(tuple(range(item_count))))

    for partition in partitions:
        options = []
        feasible = True
        for subset in partition:
            key = tuple(sorted(subset))
            if key not in subset_cache:
                subset_cache[key] = _candidate_boxes(
                    [item_dicts[index] for index in key], request.time_limit_s
                )
            if not subset_cache[key]:
                feasible = False
                break
            options.append(subset_cache[key])
        if not feasible:
            continue

        for selected in product(*options):
            cartons = []
            for carton_index, (subset, packing) in enumerate(zip(partition, selected), start=1):
                box = packing["box"]
                layout = _placement_order(packing["layout"])
                cartons.append({
                    "reference": f"{request.order_id}-CARTON-{carton_index}",
                    "item_skus": [item_dicts[index]["sku"] for index in subset],
                    "dimensions_cm": box,
                    "volume_cm3": box["length_cm"] * box["width_cm"] * box["height_cm"],
                    "actual_weight_kg": round(sum(item_dicts[index]["weight_kg"] for index in subset), 3),
                    "utilization": packing["utilization"],
                    "layout": layout,
                    "layout_objective": packing["candidate_objective"],
                    "solver_status": packing["status"],
                    "solve_time_ms": packing["solve_time_ms"],
                })
            try:
                shipping = _quote(request, cartons)
            except QuoteUnavailable:
                continue
            evaluated += 1
            total_volume = sum(carton["volume_cm3"] for carton in cartons)
            total_steps = sum(len(carton["layout"]) for carton in cartons)
            score = (
                shipping["recommended"]["total"],
                len(cartons),
                total_volume,
                total_steps,
            )
            ranked_plans.append((score, cartons, shipping))

    if not ranked_plans:
        raise ValueError("No feasible Level 2 packing and shipping plan was found")

    ranked_plans.sort(key=lambda plan: plan[0])
    score, cartons, shipping = ranked_plans[0]
    alternatives = []
    seen_signatures = {
        tuple((tuple(carton["item_skus"]), tuple(sorted(carton["dimensions_cm"].values()))) for carton in cartons)
    }
    for alternative_score, alternative_cartons, alternative_shipping in ranked_plans[1:]:
        signature = tuple(
            (tuple(carton["item_skus"]), tuple(sorted(carton["dimensions_cm"].values())))
            for carton in alternative_cartons
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        alternatives.append({
            "rank": len(alternatives) + 2,
            "carton_count": len(alternative_cartons),
            "carton_strategy": [[*carton["item_skus"]] for carton in alternative_cartons],
            "cartons": [{
                "item_skus": carton["item_skus"],
                "dimensions_cm": carton["dimensions_cm"],
            } for carton in alternative_cartons],
            "carrier": alternative_shipping["recommended"]["carrier"],
            "currency": alternative_shipping["currency"],
            "shipping_total": alternative_shipping["recommended"]["total"],
            "additional_cost": round(alternative_score[0] - score[0], 2),
            "total_carton_volume_cm3": alternative_score[2],
        })
        if len(alternatives) == 2:
            break

    return {
        "level": 2,
        "order_id": request.order_id,
        "strategy": "cost_optimized_custom_cartons",
        "destination": {
            "country_code": request.destination.upper(),
            "address": request.destination_address,
        },
        "service_type": request.service_type,
        "packing": {
            "carton_count": len(cartons),
            "cartons": cartons,
            "total_carton_volume_cm3": score[2],
            "total_actual_weight_kg": round(sum(item.weight_kg for item in request.items), 3),
            "item_count": item_count,
            "partition_count": len(partitions),
            "evaluated_plan_count": evaluated,
            "objective": "minimum_shipping_total",
        },
        "shipping": shipping,
        "recommendation": {
            "carton_strategy": [[*carton["item_skus"]] for carton in cartons],
            "carrier": shipping["recommended"]["carrier"],
            "currency": shipping["currency"],
            "shipping_total": shipping["recommended"]["total"],
        },
        "alternative_plans": alternatives,
        "scope_notices": [
            f"Level 2 searches all {len(partitions)} partitions of {item_count} cuboid(s).",
            "Each subset compares minimum-volume and compact-edge carton candidates.",
            "Carton manufacturing, padding, labor, remote-area, duties, and taxes are excluded.",
        ],
    }


def generate_level2_guide_html(result: dict) -> str:
    """Render the selected multi-carton plan as an interactive 3D guide."""
    sections = []
    groups = []
    for carton_number, carton in enumerate(result["packing"]["cartons"], start=1):
        dims = carton["dimensions_cm"]
        instructions = []
        for item in carton["layout"]:
            pos, size = item["position"], item["placed_dims"]
            instructions.append(
                "<li>第 {step} 步：放置 <b>{sku}</b>；起点 ({x:.1f}, {y:.1f}, {z:.1f}) cm；"
                "旋转后 {l:.1f}×{w:.1f}×{h:.1f} cm；{rot}（{desc}）。</li>".format(
                    step=item["step"], sku=escape(item["sku"]),
                    x=pos["x"], y=pos["y"], z=pos["z"],
                    l=size["length"], w=size["width"], h=size["height"],
                    rot=escape(item["rotation"]), desc=_ROTATION_DESCRIPTIONS[item["rotation"]],
                )
            )
        sections.append(
            f"<h2>纸箱 {carton_number} — {escape(carton['reference'])}</h2>"
            f"<p>尺寸：<b>{dims['length_cm']}×{dims['width_cm']}×{dims['height_cm']} cm</b>；"
            f"重量：{carton['actual_weight_kg']} kg；利用率：{carton['utilization']:.2%}。</p>"
            f"<ol>{''.join(instructions)}</ol>"
        )
        groups.append({"box": dims, "layout": carton["layout"]})
    rec = result["recommendation"]
    guide = (
        f"<h1>第 2 层级 3D 装箱指南 — {escape(result['order_id'])}</h1>"
        f"<p>最优方案共 <b>{result['packing']['carton_count']}</b> 箱；推荐 <b>{escape(rec['carrier'])}</b>；"
        f"费用 {escape(rec['currency'])} <b>{rec['shipping_total']:,.2f}</b>。</p>"
        "<p>坐标均以各纸箱内部左前下角为 (0,0,0)，X/Y/Z 分别沿箱长、箱宽、箱高。</p>"
        + "".join(sections)
    )
    return generate_3d_html(
        {"groups": groups}, step_mode="item",
        page_title="第 2 层级 3D 装箱指南", guide_html=guide,
    )
