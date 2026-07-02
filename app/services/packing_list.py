"""Packing list generation service.

Generates structured packing list tables from packer results,
suitable for export to clients, warehouse operations, and 3D
visualization input.

Output formats:
  - table: structured dict/list (for API JSON response)
  - text: human-readable text (for print/email)
"""

from __future__ import annotations

import math
from datetime import datetime


def generate_packing_list(packer_result: dict) -> dict:
    """Generate a comprehensive packing list from pack_items result.

    Args:
        packer_result: Output from pack_items() service.

    Returns:
        Dict with keys:
          - metadata: order-level info (strategy, total boxes, total weight, etc.)
          - boxes: list of box-level dicts, each with:
              - box_id: sequential box number
              - dimensions: box L×W×H in cm
              - items: list of per-item placement dicts
              - total_weight_kg: sum of item weights
              - utilization: volume utilization %
              - exceeds_limits: bool
          - shipping_summary: per-box shipping recommendation summary
          - generated_at: ISO timestamp
    """
    if not packer_result or not packer_result.get("groups"):
        return {
            "metadata": {"strategy": "empty", "total_boxes": 0},
            "boxes": [],
            "shipping_summary": [],
            "generated_at": datetime.now().isoformat(),
        }

    # Metadata
    metadata = {
        "strategy": packer_result["strategy"],
        "total_boxes": packer_result["total_boxes"],
        "feasible_boxes": packer_result["feasible_boxes"],
        "total_utilization": packer_result["total_utilization"],
        "total_weight_kg": sum(
            it.get("weight_kg", 0)
            for g in packer_result["groups"]
            for it in g["items"]
        ),
        "total_volume_cm3": sum(
            it["length_cm"] * it["width_cm"] * it["height_cm"]
            for g in packer_result["groups"]
            for it in g["items"]
        ),
        "group_validation": packer_result["group_validation"],
    }

    # Box-level details
    boxes = []
    shipping_summary = []

    for i, group in enumerate(packer_result["groups"]):
        box_id = i + 1

        if not group.get("box"):
            boxes.append({
                "box_id": box_id,
                "dimensions": None,
                "status": "INFEASIBLE",
                "items": [],
                "total_weight_kg": 0,
                "utilization": 0,
                "exceeds_limits": True,
            })
            continue

        box = group["box"]
        layout = group.get("layout") or []

        # Per-item placement details
        items_list = []
        for entry in layout:
            original = entry.get("original_dims") or {}
            placed = entry.get("placed_dims") or {}
            pos = entry.get("position") or {}

            items_list.append({
                "sku": entry["sku"],
                "original_dims_cm": {
                    "L": original.get("length", 0),
                    "W": original.get("width", 0),
                    "H": original.get("height", 0),
                },
                "placed_dims_cm": {
                    "L": placed.get("length", 0),
                    "W": placed.get("width", 0),
                    "H": placed.get("height", 0),
                },
                "position_cm": {
                    "x": pos.get("x", 0),
                    "y": pos.get("y", 0),
                    "z": pos.get("z", 0),
                },
                "rotation": entry.get("rotation", "?"),
                "weight_kg": entry.get("weight_kg", 0),
                "volume_cm3": round(
                    (original.get("length", 0) * original.get("width", 0) * original.get("height", 0)),
                    1,
                ),
            })

        total_weight = sum(it.get("weight_kg", 0) for it in group["items"])

        box_entry = {
            "box_id": box_id,
            "dimensions": {
                "L_cm": box["length_cm"],
                "W_cm": box["width_cm"],
                "H_cm": box["height_cm"],
            },
            "dimensions_display": (
                f"{box['length_cm']} x {box['width_cm']} x {box['height_cm']} cm"
            ),
            "box_volume_cm3": box["length_cm"] * box["width_cm"] * box["height_cm"],
            "status": group.get("status", "?"),
            "items": items_list,
            "item_count": len(items_list),
            "item_skus": [it["sku"] for it in items_list],
            "total_weight_kg": round(total_weight, 2),
            "utilization": round(group.get("utilization", 0), 4),
            "utilization_display": f"{group.get('utilization', 0):.1%}",
            "exceeds_limits": group.get("exceeds_limits", False),
            "solve_time_ms": group.get("solve_time_ms", 0),
        }

        # Dual-path info
        if group.get("dual_path"):
            dp = group["dual_path"]
            cc = dp["cost_comparison"]
            box_entry["dual_path"] = {
                "recommendation": dp["recommendation"],
                "path_a_cost_usd": cc.get("path_a_cost_usd"),
                "path_b_cost_usd": cc.get("path_b_cost_usd"),
                "savings_usd": cc.get("savings_usd"),
            }

        boxes.append(box_entry)

        # Shipping summary for this box
        ship = group.get("shipping")
        if ship and ship.get("recommended"):
            rec = ship["recommended"]
            shipping_summary.append({
                "box_id": box_id,
                "recommended_carrier": rec["carrier"],
                "recommended_cost_usd": rec["cost_usd"],
                "billable_weight_kg": rec["billable_weight_kg"],
                "dim_weight_kg": rec["dim_weight_kg"],
                "actual_weight_kg": rec["actual_weight_kg"],
                "surcharges": [
                    {"name": s["name"], "amount_usd": s["amount_usd"]}
                    for s in rec["surcharges"]
                ],
                "estimated_days": rec["estimated_days"],
            })

    return {
        "metadata": metadata,
        "boxes": boxes,
        "shipping_summary": shipping_summary,
        "generated_at": datetime.now().isoformat(),
    }


def generate_packing_list_text(packing_list: dict) -> str:
    """Generate a human-readable text version of the packing list.

    Args:
        packing_list: Output from generate_packing_list().

    Returns:
        Multi-line text string suitable for printing or email.
    """
    lines = []
    meta = packing_list["metadata"]

    lines.append("=" * 60)
    lines.append("PACKING LIST")
    lines.append("=" * 60)
    lines.append(f"Strategy: {meta['strategy']}")
    lines.append(f"Total boxes: {meta['total_boxes']}")
    lines.append(f"Total weight: {meta['total_weight_kg']} kg")
    lines.append(f"Overall utilization: {meta['total_utilization']:.1%}")
    lines.append(f"Generated: {packing_list['generated_at']}")
    lines.append("")

    for box_entry in packing_list["boxes"]:
        bid = box_entry["box_id"]
        dims = box_entry.get("dimensions_display", "N/A")
        lines.append(f"--- Box {bid}: {dims} ---")
        lines.append(f"  Weight: {box_entry['total_weight_kg']} kg")
        lines.append(f"  Utilization: {box_entry.get('utilization_display', 'N/A')}")
        if box_entry.get("exceeds_limits"):
            lines.append("  * EXCEEDS SHIPPING LIMITS")
        lines.append(f"  Status: {box_entry['status']}")

        for item in box_entry.get("items", []):
            pos = item["position_cm"]
            pd = item["placed_dims_cm"]
            lines.append(
                f"    {item['sku']}: pos({pos['x']},{pos['y']},{pos['z']}) "
                f"placed({pd['L']}x{pd['W']}x{pd['H']}) "
                f"rot={item['rotation']} wt={item['weight_kg']}kg"
            )
        lines.append("")

    # Shipping summary
    for ss in packing_list["shipping_summary"]:
        lines.append(f"Box {ss['box_id']} shipping: {ss['recommended_carrier']} "
                     f"${ss['recommended_cost_usd']} "
                     f"billable={ss['billable_weight_kg']}kg "
                     f"eta={ss['estimated_days']}d")
        for s in ss["surcharges"]:
            if s["amount_usd"] > 0:
                lines.append(f"  + {s['name']}: ${s['amount_usd']}")

    return "\n".join(lines)
