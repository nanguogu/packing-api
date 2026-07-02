"""Packing optimization service layer.

Coordinates the full packing workflow:
  1. Apply grouping constraints (grouper) → partition items into groups
  2. For each group, calculate optimal box via OR-Tools CP-SAT engine
  3. Dual-path comparison (路A vs 路B) when shipping limits are given:
     - 路A: Compliant box (within shipping limits, no surcharges)
     - 路B: Optimal minimum box (may exceed limits, with surcharges)
     → Compare total shipping cost → recommend cheapest
  4. Cross-check with py3dbp verifier (optional)
  5. Compile results with box dimensions, layout, utilization, shipping
"""

from __future__ import annotations

import logging

from app.core.engine import calculate_min_box, calculate_min_box_with_limits
from app.core.verifier import cross_check_engine_result
from app.services.grouper import apply_group_rules, validate_groups
from app.services.shipping import get_shipping_recommendation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dual-path comparison
# ---------------------------------------------------------------------------

def _compute_dual_path(
    group_items: list[dict],
    shipping_limits: dict,
    destination: str | int | None,
    time_limit_s: float,
    workers: int,
) -> dict:
    """Compute both 路A (compliant) and 路B (optimal minimum) paths,
    then compare total shipping costs and recommend the cheaper option.

    Args:
        group_items: Items in this packing group.
        shipping_limits: Dict with max_length_cm, max_width_cm, max_height_cm, max_weight_kg.
        destination: Shipping zone for rate comparison.
        time_limit_s: Solver time limit.
        workers: Number of search threads.

    Returns:
        Dict with keys:
          - path_a: result for compliant box (or None if infeasible)
          - path_b: result for minimum envelope box
          - recommendation: "path_a" | "path_b" | "path_a_only" | "path_b_only"
          - cost_comparison: dict with both paths' total shipping costs
          - best_path: the winning path's full result dict
    """
    total_weight = sum(it.get("weight_kg", 0) for it in group_items)

    # Path B: Optimal minimum box (unconstrained — may exceed limits)
    path_b_result = calculate_min_box(
        group_items, time_limit_s=time_limit_s, workers=workers
    )

    # Path A: Compliant box (constrained by shipping limits)
    path_a_result = calculate_min_box_with_limits(
        group_items,
        shipping_limits=shipping_limits,
        time_limit_s=time_limit_s,
        workers=workers,
    )

    # Compute shipping costs for each path
    path_a_cost = None
    path_b_cost = None
    path_a_shipping = None
    path_b_shipping = None

    # 路A feasibility: must have a valid box AND the box dims must actually
    # comply with shipping limits. When the constrained solve fails, the
    # engine may return the unconstrained result (which exceeds limits).
    # We explicitly check that the Path A box fits within the limits.
    path_a_feasible = False
    if path_a_result and path_a_result.get("success", False) and path_a_result["box"]:
        pa = path_a_result["box"]
        dims_sorted = sorted([pa["length_cm"], pa["width_cm"], pa["height_cm"]])
        limits_sorted = sorted([
            shipping_limits.get("max_length_cm", 9999),
            shipping_limits.get("max_width_cm", 9999),
            shipping_limits.get("max_height_cm", 9999),
        ])
        # Each sorted dim must fit within corresponding sorted limit (+1cm rounding tolerance)
        within_limits = all(d <= l + 1 for d, l in zip(dims_sorted, limits_sorted))
        if within_limits:
            path_a_feasible = True
        else:
            logger.info("Path A box exceeds limits -> not feasible as compliant path")

    if path_a_result and path_a_result["box"] and path_a_feasible:
        try:
            path_a_shipping = get_shipping_recommendation(
                path_a_result["box"], total_weight, destination or "intl"
            )
            if path_a_shipping["recommended"]:
                path_a_cost = path_a_shipping["recommended"]["cost_usd"]
        except Exception as e:
            logger.warning(f"Path A shipping calc failed: {e}")

    if path_b_result and path_b_result["box"]:
        # 路B: minimum box → may have surcharges if exceeds limits
        try:
            path_b_shipping = get_shipping_recommendation(
                path_b_result["box"], total_weight, destination or "intl"
            )
            if path_b_shipping["recommended"]:
                path_b_cost = path_b_shipping["recommended"]["cost_usd"]
        except Exception as e:
            logger.warning(f"Path B shipping calc failed: {e}")

    # Determine recommendation
    recommendation = "path_b_only"
    best_path = path_b_result
    best_shipping = path_b_shipping

    if path_a_result and path_a_cost is not None and path_b_cost is not None:
        # Both paths feasible → compare costs
        if path_a_cost <= path_b_cost:
            recommendation = "path_a"
            best_path = path_a_result
            best_shipping = path_a_shipping
            logger.info(
                f"Path A wins: compliant ${path_a_cost} <= optimal+surcharge ${path_b_cost}"
            )
        else:
            recommendation = "path_b"
            logger.info(
                f"Path B wins: optimal+surcharge ${path_b_cost} < compliant ${path_a_cost}"
            )
    elif path_a_result and path_a_cost is not None:
        # Only path A has shipping data
        recommendation = "path_a_only"
        best_path = path_a_result
        best_shipping = path_a_shipping
    elif path_b_result:
        # Only path B available
        recommendation = "path_b_only"

    # Build cost comparison
    cost_comparison = {
        "path_a_cost_usd": path_a_cost,
        "path_b_cost_usd": path_b_cost,
        "path_a_exceeds_limits": path_a_result.get("exceeds_limits", True) if path_a_result else None,
        "path_b_exceeds_limits": True,  # Path B is unconstrained, may exceed
        "savings_usd": None,
    }

    if path_a_cost is not None and path_b_cost is not None:
        cost_comparison["savings_usd"] = round(
            abs(path_a_cost - path_b_cost), 2
        )
        cost_comparison["savings_path"] = recommendation

    return {
        "path_a": path_a_result,
        "path_a_shipping": path_a_shipping,
        "path_b": path_b_result,
        "path_b_shipping": path_b_shipping,
        "recommendation": recommendation,
        "cost_comparison": cost_comparison,
        "best_path": best_path,
        "best_shipping": best_shipping,
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def pack_items(
    items: list[dict],
    group_rules: list[dict] | None = None,
    shipping_limits: dict | None = None,
    destination: str | int | None = None,
    time_limit_s: float = 8.0,
    workers: int = 8,
    verify: bool = True,
    dual_path: bool = False,
) -> dict:
    """Run the full packing pipeline for a list of items.

    Pipeline steps:
      1. Apply grouping constraints → partition items into groups
      2. For each group, calculate optimal box via OR-Tools CP-SAT engine
      3. Dual-path comparison (路A vs 路B) if shipping_limits given and dual_path=True
      4. (Optional) Cross-check with py3dbp verifier
      5. Compile all group results into a comprehensive packing plan

    Args:
        items: List of item dicts, each with keys:
            sku, length_cm, width_cm, height_cm, weight_kg
        group_rules: Optional grouping constraint rules
        shipping_limits: Optional dict with max_length_cm, max_width_cm,
            max_height_cm, max_weight_kg for P0 constraint enforcement
        destination: Shipping zone for rate comparison.
        time_limit_s: CP-SAT solver time limit per group. Default 8s.
        workers: Number of parallel search threads. Default 8.
        verify: Whether to cross-check with py3dbp. Default True.
        dual_path: Whether to compute dual-path (路A vs 路B) comparison.
            Default False. When True and shipping_limits are provided,
            computes both compliant and optimal-minimum paths and
            recommends the one with lower total shipping cost.

    Returns:
        Dict with keys:
          - strategy: "single_box" | "multi_box" | "mixed"
          - groups: list of group result dicts
          - total_boxes: number of boxes needed
          - total_utilization: overall utilization across all groups
          - group_validation: dict from validate_groups()
          - summary: human-readable packing summary
    """
    if not items:
        return {
            "strategy": "empty",
            "groups": [],
            "total_boxes": 0,
            "total_utilization": 0,
            "group_validation": {"valid": True, "violations": []},
            "summary": "No items to pack.",
        }

    # Step 1: Apply grouping constraints
    rules = group_rules or []
    groups = apply_group_rules(items, rules, shipping_limits)

    # Validate the grouping
    group_validation = validate_groups(groups, rules)
    if not group_validation["valid"]:
        logger.warning(f"P1 violations detected: {group_validation['violations']}")

    # Step 2: For each group, solve packing + (optional) dual-path
    group_results = []
    for group_items in groups:
        total_weight = sum(it.get("weight_kg", 0) for it in group_items)

        # --- Dual-path mode ---
        if dual_path and shipping_limits:
            dual = _compute_dual_path(
                group_items, shipping_limits, destination,
                time_limit_s, workers,
            )
            best_path = dual["best_path"]

            if not best_path:
                # Both paths failed
                group_results.append({
                    "items": group_items,
                    "box": None,
                    "layout": None,
                    "utilization": 0,
                    "solve_time_ms": 0,
                    "exceeds_limits": True,
                    "status": "INFEASIBLE",
                    "verification": None,
                    "shipping": None,
                    "dual_path": dual,
                })
                continue

            # Verification on best path only
            verification = None
            if verify:
                verification = cross_check_engine_result(best_path, group_items)

            group_results.append({
                "items": group_items,
                "box": best_path["box"],
                "layout": best_path["layout"],
                "utilization": best_path["utilization"],
                "solve_time_ms": best_path["solve_time_ms"],
                "exceeds_limits": best_path.get("exceeds_limits", False),
                "status": best_path.get("status", "UNKNOWN"),
                "verification": verification,
                "shipping": dual["best_shipping"],
                "dual_path": dual,
            })
            continue

        # --- Single-path mode (original logic) ---
        if shipping_limits:
            engine_result = calculate_min_box_with_limits(
                group_items,
                shipping_limits=shipping_limits,
                time_limit_s=time_limit_s,
                workers=workers,
            )
        else:
            engine_result = calculate_min_box(
                group_items,
                time_limit_s=time_limit_s,
                workers=workers,
            )

        if not engine_result:
            group_results.append({
                "items": group_items,
                "box": None,
                "layout": None,
                "utilization": 0,
                "solve_time_ms": 0,
                "exceeds_limits": True,
                "status": "INFEASIBLE",
                "verification": None,
                "shipping": None,
            })
            continue

        # Verification
        verification = None
        if verify:
            verification = cross_check_engine_result(engine_result, group_items)

        # Shipping recommendation
        shipping_rec = None
        if engine_result["box"] and total_weight > 0:
            try:
                shipping_rec = get_shipping_recommendation(
                    engine_result["box"], total_weight, destination or "intl"
                )
            except Exception as e:
                logger.warning(f"Shipping recommendation failed: {e}")

        group_results.append({
            "items": group_items,
            "box": engine_result["box"],
            "layout": engine_result["layout"],
            "utilization": engine_result["utilization"],
            "solve_time_ms": engine_result["solve_time_ms"],
            "exceeds_limits": engine_result.get("exceeds_limits", False),
            "status": engine_result.get("status", "UNKNOWN"),
            "verification": verification,
            "shipping": shipping_rec,
        })

    # Step 3: Compile summary
    total_boxes = len(group_results)
    feasible_boxes = sum(1 for r in group_results if r["box"] is not None)

    total_item_vol = sum(
        it["length_cm"] * it["width_cm"] * it["height_cm"]
        for it in items
    )
    total_box_vol = sum(
        r["box"]["length_cm"] * r["box"]["width_cm"] * r["box"]["height_cm"]
        for r in group_results if r["box"]
    )
    overall_util = round(total_item_vol / total_box_vol, 4) if total_box_vol > 0 else 0

    # Strategy classification
    if total_boxes == 1 and feasible_boxes == 1:
        strategy = "single_box"
    elif total_boxes > 1 and feasible_boxes == total_boxes:
        strategy = "multi_box"
    elif feasible_boxes < total_boxes:
        strategy = "mixed"
    else:
        strategy = "unknown"

    # Build human-readable summary
    summary_lines = []
    for i, r in enumerate(group_results):
        if r["box"]:
            skus = [it["sku"] for it in r["items"]]
            box_str = (
                f"{r['box']['length_cm']}x{r['box']['width_cm']}x{r['box']['height_cm']}cm"
            )
            summary_lines.append(
                f"Box {i + 1}: {box_str}, util={r['utilization']:.1%}, items={skus}"
            )
            if r.get("exceeds_limits"):
                summary_lines.append("  * Exceeds shipping limits")
            # Dual-path info
            if r.get("dual_path"):
                dual = r["dual_path"]
                cc = dual["cost_comparison"]
                summary_lines.append(
                    f"  Dual-path: {dual['recommendation']}"
                )
                if cc["path_a_cost_usd"] is not None:
                    summary_lines.append(
                        f"    Path A (compliant): ${cc['path_a_cost_usd']}"
                    )
                if cc["path_b_cost_usd"] is not None:
                    summary_lines.append(
                        f"    Path B (optimal+surcharge): ${cc['path_b_cost_usd']}"
                    )
                if cc["savings_usd"] is not None:
                    summary_lines.append(
                        f"    Savings: ${cc['savings_usd']} ({cc['savings_path']})"
                    )
            # Shipping info
            if r.get("shipping") and r["shipping"]["recommended"]:
                rec = r["shipping"]["recommended"]
                summary_lines.append(
                    f"  Shipping: {rec['carrier']} ${rec['cost_usd']}"
                )
        else:
            summary_lines.append(
                f"Box {i + 1}: INFEASIBLE"
            )

    summary = "\n".join(summary_lines)

    return {
        "strategy": strategy,
        "groups": group_results,
        "total_boxes": total_boxes,
        "feasible_boxes": feasible_boxes,
        "total_utilization": overall_util,
        "group_validation": group_validation,
        "summary": summary,
    }
