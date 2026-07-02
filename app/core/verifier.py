"""py3dbp 3D bin-packing layout verification wrapper.

Uses py3dbp (jerry800416 improved version) as an independent cross-check
against the OR-Tools engine result. If py3dbp can also find a valid placement
for all items within the calculated box dimensions, the engine result is
confirmed. If py3dbp fails, it may indicate:
  - The engine result has a subtle error (unlikely with CP-SAT)
  - py3dbp's greedy heuristic didn't find a placement (possible)
  - Items truly cannot fit (definite failure)

Coordinate mapping:
  Our engine uses (length, width, height) in cm.
  py3dbp uses (width, height, depth) in arbitrary units.
  We map: engine.L → py3dbp.width, engine.W → py3dbp.height, engine.H → py3dbp.depth
"""

from __future__ import annotations

import sys
import os

# Add py3dbp local package to path
_PY3DBP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),  # app/core → packing-api
    "3D-bin-packing",
)
if _PY3DBP_PATH not in sys.path:
    sys.path.insert(0, _PY3DBP_PATH)

from py3dbp import Packer, Bin, Item


def verify_layout(
    box: dict,
    items: list[dict],
    max_weight_kg: float = 999999,
    fix_point: bool = True,
    check_stable: bool = False,
    support_surface_ratio: float = 0.0,
) -> dict:
    """Verify that all items can be physically placed inside the given box.

    Uses py3dbp's greedy heuristic packing algorithm as an independent
    verification of the OR-Tools engine result.

    Args:
        box: Dict with keys: length_cm, width_cm, height_cm.
        items: List of item dicts, each with:
            - sku (str): product identifier
            - length_cm (float): product length
            - width_cm (float): product width
            - height_cm (float): product height
            - weight_kg (float, optional): product weight
        max_weight_kg: Maximum weight the box can hold. Default 999999 (no limit).
        fix_point: Whether py3dbp should fix floating point positions. Default True.
        check_stable: Whether to check item stability (support surface). Default False.
        support_surface_ratio: Minimum support ratio for stability. Default 0.0.

    Returns:
        Dict with keys:
          - valid (bool): True if ALL items fit inside the box
          - layout (list): per-item placement dicts with position and rotation
          - unplaced (list): list of SKUs that could not be placed
          - utilization (float): volume utilization ratio (0-1)
          - box_volume_cm3 (float): total box volume
          - items_volume_cm3 (float): total items volume
    """
    if not items:
        return {
            "valid": True,
            "layout": [],
            "unplaced": [],
            "utilization": 0.0,
            "box_volume_cm3": 0.0,
            "items_volume_cm3": 0.0,
        }

    packer = Packer()

    # Create py3dbp Bin from our box dimensions
    # Mapping: length → width, width → height, height → depth
    box_L = box["length_cm"]
    box_W = box["width_cm"]
    box_H = box["height_cm"]

    pb = Bin(
        partno="VERIFY-BOX",
        WHD=(box_L, box_W, box_H),  # (width=L, height=W, depth=H)
        max_weight=max_weight_kg,
        corner=0,
        put_type=1,
    )
    packer.addBin(pb)

    # Create py3dbp Items from our item dimensions
    for it in items:
        sku = it["sku"]
        item_L = it["length_cm"]
        item_W = it["width_cm"]
        item_H = it["height_cm"]
        item_weight = it.get("weight_kg", 0)

        pi = Item(
            partno=sku,
            name=sku,
            typeof="cube",
            WHD=(item_L, item_W, item_H),  # (width=L, height=W, depth=H)
            weight=item_weight,
            level=1,
            loadbear=0,
            updown=True,  # allow all rotations for verification
            color="#1f77b4",
        )
        packer.addItem(pi)

    # Run packing
    packer.pack(
        bigger_first=True,
        distribute_items=True,
        fix_point=fix_point,
        check_stable=check_stable,
        support_surface_ratio=support_surface_ratio,
        number_of_decimals=3,
    )

    # Extract results
    packed_bin = packer.bins[0]
    placed_items = packed_bin.items
    unplaced_items = packed_bin.unfitted_items

    # Calculate volumes
    box_volume_cm3 = box_L * box_W * box_H
    items_volume_cm3 = sum(
        it["length_cm"] * it["width_cm"] * it["height_cm"] for it in items
    )

    # Build layout output
    layout = []
    for pi in placed_items:
        # py3dbp position: [width_pos, height_pos, depth_pos]
        # Map back to our coordinate system:
        #   py3dbp width_pos → our x (length axis)
        #   py3dbp height_pos → our y (width axis)
        #   py3dbp depth_pos → our z (height axis)
        pos = pi.position
        dims = pi.getDimension()  # [width, height, depth] in py3dbp terms

        # Determine rotation type name
        # py3dbp rotation types: RT_WHD(0), RT_HWD(1), RT_HDW(2), RT_DHW(3), RT_DWH(4), RT_WDH(5)
        rot_names = {
            0: "LWH",   # WHD → width=L, height=W, depth=H (no rotation)
            1: "WLH",   # HWD → height=L becomes width, width=W becomes height, depth=H
            2: "HLW",   # HDW → height=L becomes width, depth=W becomes height, width=H becomes depth
            3: "WHL",   # DHW → depth=H becomes width... actually let's just use the rotation number
            4: "HWL",   # DWH
            5: "LHW",   # WDH
        }

        layout.append({
            "sku": pi.partno,
            "position": {
                # Map py3dbp coords → our coords
                "x": float(pos[0]),  # py3dbp width axis → our length axis
                "y": float(pos[1]),  # py3dbp height axis → our width axis
                "z": float(pos[2]),  # py3dbp depth axis → our height axis
            },
            "placed_dims": {
                # py3dbp getDimension() returns [width, height, depth]
                # width → our length, height → our width, depth → our height
                "length": float(dims[0]),
                "width": float(dims[1]),
                "height": float(dims[2]),
            },
            "rotation": rot_names.get(pi.rotation_type, f"RT_{pi.rotation_type}"),
            "weight_kg": float(pi.weight),
        })

    unplaced = [pi.partno for pi in unplaced_items]
    valid = len(unplaced) == 0
    utilization = round(items_volume_cm3 / box_volume_cm3, 4) if box_volume_cm3 > 0 else 0

    return {
        "valid": valid,
        "layout": layout,
        "unplaced": unplaced,
        "utilization": utilization,
        "box_volume_cm3": box_volume_cm3,
        "items_volume_cm3": items_volume_cm3,
    }


def cross_check_engine_result(
    engine_result: dict,
    items: list[dict],
) -> dict:
    """Cross-check an engine result against py3dbp verification.

    Takes the output from calculate_min_box() and independently verifies
    whether py3dbp can also pack all items into the same box dimensions.

    Args:
        engine_result: Output dict from calculate_min_box().
        items: Original items list (same format as engine input).

    Returns:
        Dict with keys:
          - engine_valid (bool): engine reported success
          - verifier_valid (bool): py3dbp confirmed all items fit
          - consistent (bool): engine and verifier agree
          - engine_layout (list): engine's layout
          - verifier_layout (list): py3dbp's layout
          - unplaced (list): items py3dbp couldn't place
          - engine_utilization (float): engine's utilization
          - verifier_utilization (float): py3dbp's utilization
    """
    if not engine_result or not engine_result.get("success"):
        return {
            "engine_valid": False,
            "verifier_valid": False,
            "consistent": True,  # both agree: no valid result
            "engine_layout": [],
            "verifier_layout": [],
            "unplaced": [],
            "engine_utilization": 0,
            "verifier_utilization": 0,
        }

    box = engine_result["box"]
    verify_result = verify_layout(box, items)

    # Check consistency: engine says all fit, verifier should also confirm
    # But note: py3dbp uses greedy heuristic, so it may fail even when
    # a valid placement exists (engine found optimal via CP-SAT).
    # Inconsistency means we need manual review, not necessarily engine error.
    engine_valid = True
    verifier_valid = verify_result["valid"]

    # If verifier fails but engine succeeds, this is "possible disagreement"
    # — py3dbp heuristic limitations, not engine bugs
    consistent = engine_valid == verifier_valid

    return {
        "engine_valid": engine_valid,
        "verifier_valid": verifier_valid,
        "consistent": consistent,
        "note": (
            "If verifier fails but engine succeeds, py3dbp's greedy heuristic "
            "may not find a valid placement even though one exists (CP-SAT "
            "guarantees optimality). Manual review recommended."
        ) if not consistent else None,
        "engine_layout": engine_result["layout"],
        "verifier_layout": verify_result["layout"],
        "unplaced": verify_result["unplaced"],
        "engine_utilization": engine_result["utilization"],
        "verifier_utilization": verify_result["utilization"],
        "verifier_detail": verify_result,
    }
