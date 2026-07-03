"""OR-Tools bin-packing engine wrapper.

Core optimization engine that determines the minimum box size for a given
set of rectangular items. Uses boxing-match's CP-SAT approach adapted
for our "定箱" (cartonization) requirement.

Strategy:
  Instead of enumerating candidate box sizes, we use a large "virtual box"
  and let OR-Tools minimize maxX+maxY+maxZ — the resulting envelope is
  the minimum packing box. This avoids brute-force enumeration and gives
  us a direct optimal solution.

For multi-box scenarios (items exceed shipping limits):
  We split items into groups first (grouper.py), then solve each group
  independently.
"""

from __future__ import annotations

import math
import time
from ortools.sat.python import cp_model


# All internal calculations use mm for integer precision (CP-SAT requires integers)
CM_TO_MM = 10
MM_TO_CM = 0.1


def calculate_min_box(
    items: list[dict],
    time_limit_s: float = 8.0,
    workers: int = 8,
    upright_only: bool = False,
    objective: str = "edge_sum",
    require_support: bool = False,
) -> dict | None:
    """Calculate the minimum bounding box for a set of rectangular items.

    Uses a large virtual container and OR-Tools CP-SAT to minimize the
    packing envelope (maxX + maxY + maxZ). The result gives the smallest
    box that can contain all items with no overlap.

    Args:
        items: List of dicts, each with keys:
            - sku (str): product identifier
            - length_cm (float): product length in cm
            - width_cm (float): product width in cm
            - height_cm (float): product height in cm
            - weight_kg (float, optional): product weight (not used in solver)
        time_limit_s: CP-SAT solver time limit in seconds. Default 8s.
        workers: Number of parallel search threads. Default 8.
        upright_only: If True, only allow 2 rotation types (no flipping).
        objective: ``edge_sum`` for legacy behavior or ``volume`` for the
            smallest custom carton by cubic volume.
        require_support: If True, every item must sit on the carton floor or
            be fully supported by the top face of one other item.

    Returns:
        Dict with keys:
            - success (bool): whether solver found a feasible solution
            - box (dict): {"length_cm", "width_cm", "height_cm"} minimum box dims
            - layout (list): per-item placement info with position and rotation
            - utilization (float): volume utilization ratio
            - solve_time_ms (int): solver wall time in milliseconds
        Returns None if solver fails (items too large for any reasonable box).
    """
    if not items:
        return None
    if objective not in {"edge_sum", "volume"}:
        raise ValueError(f"Unsupported packing objective: {objective}")

    n = len(items)
    model = cp_model.CpModel()

    # Rotation permutations
    all_perms = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]
    upright_perms = [(0, 1, 2), (1, 0, 2)]

    # Convert cm to mm (integer) for CP-SAT
    item_dims_mm = []
    for it in items:
        dims = [
            int(round(it["length_cm"] * CM_TO_MM)),
            int(round(it["width_cm"] * CM_TO_MM)),
            int(round(it["height_cm"] * CM_TO_MM)),
        ]
        item_dims_mm.append(dims)

    # Every item may rotate onto every axis. The sum of each item's largest
    # edge is therefore a safe bound for all three virtual-box dimensions.
    max_axis = sum(max(d) for d in item_dims_mm)
    max_L = max_W = max_H = max_axis

    # Decision variables
    x = [model.NewIntVar(0, max_L, f"x_{i}") for i in range(n)]
    y = [model.NewIntVar(0, max_W, f"y_{i}") for i in range(n)]
    z = [model.NewIntVar(0, max_H, f"z_{i}") for i in range(n)]
    sx = [model.NewIntVar(0, max_L, f"sx_{i}") for i in range(n)]
    sy = [model.NewIntVar(0, max_W, f"sy_{i}") for i in range(n)]
    sz = [model.NewIntVar(0, max_H, f"sz_{i}") for i in range(n)]

    # Rotation selection variables
    for i in range(n):
        perms = upright_perms if upright_only else all_perms
        o_i = [model.NewBoolVar(f"o_{i}_{k}") for k in range(len(perms))]
        model.Add(sum(o_i) == 1)
        dims = item_dims_mm[i]
        model.Add(sx[i] == sum(o_i[k] * dims[perms[k][0]] for k in range(len(perms))))
        model.Add(sy[i] == sum(o_i[k] * dims[perms[k][1]] for k in range(len(perms))))
        model.Add(sz[i] == sum(o_i[k] * dims[perms[k][2]] for k in range(len(perms))))

    # No-overlap constraints (3D decomposition)
    for i in range(n):
        for j in range(i + 1, n):
            bix = model.NewBoolVar(f"ix_{i}_{j}")
            biy = model.NewBoolVar(f"iy_{i}_{j}")
            biz = model.NewBoolVar(f"iz_{i}_{j}")
            bjx = model.NewBoolVar(f"jx_{i}_{j}")
            bjy = model.NewBoolVar(f"jy_{i}_{j}")
            bjz = model.NewBoolVar(f"jz_{i}_{j}")

            model.Add(x[i] + sx[i] <= x[j]).OnlyEnforceIf(bix)
            model.Add(x[j] + sx[j] <= x[i]).OnlyEnforceIf(bjx)
            model.Add(y[i] + sy[i] <= y[j]).OnlyEnforceIf(biy)
            model.Add(y[j] + sy[j] <= y[i]).OnlyEnforceIf(bjy)
            model.Add(z[i] + sz[i] <= z[j]).OnlyEnforceIf(biz)
            model.Add(z[j] + sz[j] <= z[i]).OnlyEnforceIf(bjz)

            model.AddBoolOr([bix, biy, biz, bjx, bjy, bjz])

    if require_support:
        for i in range(n):
            on_floor = model.NewBoolVar(f"floor_{i}")
            model.Add(z[i] == 0).OnlyEnforceIf(on_floor)
            support_options = [on_floor]
            for j in range(n):
                if i == j:
                    continue
                supported_by_j = model.NewBoolVar(f"support_{i}_by_{j}")
                model.Add(z[i] == z[j] + sz[j]).OnlyEnforceIf(supported_by_j)
                model.Add(x[i] >= x[j]).OnlyEnforceIf(supported_by_j)
                model.Add(x[i] + sx[i] <= x[j] + sx[j]).OnlyEnforceIf(supported_by_j)
                model.Add(y[i] >= y[j]).OnlyEnforceIf(supported_by_j)
                model.Add(y[i] + sy[i] <= y[j] + sy[j]).OnlyEnforceIf(supported_by_j)
                support_options.append(supported_by_j)
            model.Add(sum(support_options) == 1)

    # Objective: minimize packing envelope
    maxX = model.NewIntVar(0, max_L, "maxX")
    maxY = model.NewIntVar(0, max_W, "maxY")
    maxZ = model.NewIntVar(0, max_H, "maxZ")
    for i in range(n):
        model.Add(maxX >= x[i] + sx[i])
        model.Add(maxY >= y[i] + sy[i])
        model.Add(maxZ >= z[i] + sz[i])
    if objective == "volume":
        max_area = max_L * max_W
        max_volume = max_area * max_H
        if max_volume > 9_000_000_000_000_000_000:
            raise ValueError("Item dimensions are too large for volume optimization")
        area = model.NewIntVar(0, max_area, "boxArea")
        volume = model.NewIntVar(0, max_volume, "boxVolume")
        model.AddMultiplicationEquality(area, [maxX, maxY])
        model.AddMultiplicationEquality(volume, [area, maxZ])
        model.Minimize(volume)
    else:
        model.Minimize(maxX + maxY + maxZ)

    # Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = workers

    start = time.time()
    status = solver.Solve(model)
    primary_status = status

    # Volume can have many equivalent factorisations (for example a very long,
    # thin carton and a balanced carton with the same cubic volume). Once the
    # minimum volume is proven, prefer the smaller edge sum and then compact
    # coordinates without ever sacrificing that primary optimum.
    tie_break_status = None
    if objective == "volume" and status == cp_model.OPTIMAL:
        optimal_volume = solver.Value(volume)
        model.Add(volume == optimal_volume)
        coordinate_sum = sum(x) + sum(y) + sum(z)
        coordinate_bound = 3 * n * max_axis
        model.Minimize(
            (maxX + maxY + maxZ) * (coordinate_bound + 1) + coordinate_sum
        )
        remaining_s = time_limit_s - (time.time() - start)
        if remaining_s > 0.05:
            tie_solver = cp_model.CpSolver()
            tie_solver.parameters.max_time_in_seconds = remaining_s
            tie_solver.parameters.num_search_workers = workers
            tie_break_status = tie_solver.Solve(model)
            if tie_break_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                solver = tie_solver

    elapsed_ms = int((time.time() - start) * 1000)

    if primary_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    # Extract results — convert mm back to cm
    box_L_mm = solver.Value(maxX)
    box_W_mm = solver.Value(maxY)
    box_H_mm = solver.Value(maxZ)

    # Round up to nearest cm for practical box sizes
    # Never round the containing box down: layout coordinates retain 0.1cm
    # precision, so normal round() could return a box smaller than its contents.
    box_L_cm = max(1, math.ceil(box_L_mm * MM_TO_CM))  # at least 1cm
    box_W_cm = max(1, math.ceil(box_W_mm * MM_TO_CM))
    box_H_cm = max(1, math.ceil(box_H_mm * MM_TO_CM))

    box_volume_cm3 = box_L_cm * box_W_cm * box_H_cm

    # Build layout
    layout = []
    all_rot_names = ["LWH", "LHW", "WLH", "WHL", "HLW", "HWL"]
    upright_rot_names = ["LWH", "WLH"]
    items_volume_cm3 = 0

    for i in range(n):
        px = round(solver.Value(x[i]) * MM_TO_CM, 1)
        py = round(solver.Value(y[i]) * MM_TO_CM, 1)
        pz = round(solver.Value(z[i]) * MM_TO_CM, 1)
        psx = round(solver.Value(sx[i]) * MM_TO_CM, 1)
        psy = round(solver.Value(sy[i]) * MM_TO_CM, 1)
        psz = round(solver.Value(sz[i]) * MM_TO_CM, 1)

        # Determine rotation type
        orig_dims = [items[i]["length_cm"], items[i]["width_cm"], items[i]["height_cm"]]
        rot_name = "unknown"
        if upright_only:
            perms = upright_perms
            rot_names = upright_rot_names
        else:
            perms = all_perms
            rot_names = all_rot_names

        for k, perm in enumerate(perms):
            expected = [orig_dims[perm[0]], orig_dims[perm[1]], orig_dims[perm[2]]
]
            if (abs(psx - expected[0]) < 0.2 and
                abs(psy - expected[1]) < 0.2 and
                abs(psz - expected[2]) < 0.2):
                rot_name = rot_names[k]
                break

        item_vol = items[i]["length_cm"] * items[i]["width_cm"] * items[i]["height_cm"]
        items_volume_cm3 += item_vol

        layout.append({
            "sku": items[i]["sku"],
            "position": {"x": px, "y": py, "z": pz},
            "placed_dims": {"length": psx, "width": psy, "height": psz},
            "rotation": rot_name,
            "original_dims": {
                "length": items[i]["length_cm"],
                "width": items[i]["width_cm"],
                "height": items[i]["height_cm"],
            },
            "weight_kg": items[i].get("weight_kg", 0),
        })

    utilization = round(items_volume_cm3 / box_volume_cm3, 4) if box_volume_cm3 > 0 else 0

    return {
        "success": True,
        "box": {
            "length_cm": box_L_cm,
            "width_cm": box_W_cm,
            "height_cm": box_H_cm,
        },
        "layout": layout,
        "utilization": utilization,
        "solve_time_ms": elapsed_ms,
        "status": "OPTIMAL" if primary_status == cp_model.OPTIMAL else "FEASIBLE",
        "tie_break_status": (
            "OPTIMAL" if tie_break_status == cp_model.OPTIMAL
            else "FEASIBLE" if tie_break_status == cp_model.FEASIBLE
            else None
        ),
        "objective": objective,
        "support_required": require_support,
    }


def calculate_min_box_with_limits(
    items: list[dict],
    shipping_limits: dict | None = None,
    time_limit_s: float = 8.0,
    workers: int = 8,
) -> dict:
    """Calculate minimum box WITH shipping constraint enforcement.

    This is the "路B" (compliant path) — adds hard constraints for
    shipping limits (max length, max weight, etc.) into the CP-SAT model.

    Args:
        items: Same format as calculate_min_box.
        shipping_limits: Dict with optional keys:
            - max_length_cm (float): box length must not exceed this
            - max_width_cm (float): box width must not exceed this
            - max_height_cm (float): box height must not exceed this
            - max_weight_kg (float): total weight must not exceed this
        time_limit_s: Solver time limit.
        workers: Number of search threads.

    Returns:
        Same format as calculate_min_box, but with additional key:
            - exceeds_limits (bool): whether the unconstrained solution
              would have exceeded limits (useful for comparing 路A vs 路B)
    """
    # First solve unconstrained (路A)
    result_unconstrained = calculate_min_box(items, time_limit_s, workers)

    # Then solve constrained (路B) if limits are provided
    if shipping_limits and result_unconstrained:
        max_L = int(round(shipping_limits.get("max_length_cm", 9999) * CM_TO_MM))
        max_W = int(round(shipping_limits.get("max_width_cm", 9999) * CM_TO_MM))
        max_H = int(round(shipping_limits.get("max_height_cm", 9999) * CM_TO_MM))

        # Check if unconstrained result already fits within limits
        box = result_unconstrained["box"]
        exceeds = False
        if max_L < 99990 and box["length_cm"] > shipping_limits.get("max_length_cm", 9999):
            exceeds = True
        if max_W < 99990 and box["width_cm"] > shipping_limits.get("max_width_cm", 9999):
            exceeds = True
        if max_H < 99990 and box["height_cm"] > shipping_limits.get("max_height_cm", 9999):
            exceeds = True

        if exceeds:
            # Re-solve with hard limits on the envelope
            result_constrained = _solve_with_box_limits(
                items, max_L, max_W, max_H, time_limit_s, workers
            )
            if result_constrained:
                result_constrained["exceeds_limits"] = True
                return result_constrained
            # If constrained solve fails → items truly can't fit within limits
            result_unconstrained["exceeds_limits"] = True
            result_unconstrained["limit_fit_possible"] = False
            return result_unconstrained

        result_unconstrained["exceeds_limits"] = False
    elif result_unconstrained:
        result_unconstrained["exceeds_limits"] = False

    return result_unconstrained


def _solve_with_box_limits(
    items: list[dict],
    max_L_mm: int,
    max_W_mm: int,
    max_H_mm: int,
    time_limit_s: float = 8.0,
    workers: int = 8,
) -> dict | None:
    """Solve with hard box dimension constraints (shipping limits).

    The maxX, maxY, maxZ are constrained to not exceed the shipping limits.
    If the solver can't find a solution, items can't fit in a compliant box.
    """
    n = len(items)
    model = cp_model.CpModel()

    all_perms = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]

    item_dims_mm = []
    for it in items:
        dims = [
            int(round(it["length_cm"] * CM_TO_MM)),
            int(round(it["width_cm"] * CM_TO_MM)),
            int(round(it["height_cm"] * CM_TO_MM)),
        ]
        item_dims_mm.append(dims)

    # Position and size variables (bounded by shipping limits)
    x = [model.NewIntVar(0, max_L_mm, f"x_{i}") for i in range(n)]
    y = [model.NewIntVar(0, max_W_mm, f"y_{i}") for i in range(n)]
    z = [model.NewIntVar(0, max_H_mm, f"z_{i}") for i in range(n)]
    sx = [model.NewIntVar(0, max_L_mm, f"sx_{i}") for i in range(n)]
    sy = [model.NewIntVar(0, max_W_mm, f"sy_{i}") for i in range(n)]
    sz = [model.NewIntVar(0, max_H_mm, f"sz_{i}") for i in range(n)]

    # Rotation + boundary constraints
    for i in range(n):
        perms = all_perms
        o_i = [model.NewBoolVar(f"o_{i}_{k}") for k in range(len(perms))]
        model.Add(sum(o_i) == 1)
        dims = item_dims_mm[i]
        model.Add(sx[i] == sum(o_i[k] * dims[perms[k][0]] for k in range(len(perms))))
        model.Add(sy[i] == sum(o_i[k] * dims[perms[k][1]] for k in range(len(perms))))
        model.Add(sz[i] == sum(o_i[k] * dims[perms[k][2]] for k in range(len(perms))))

        model.Add(x[i] + sx[i] <= max_L_mm)
        model.Add(y[i] + sy[i] <= max_W_mm)
        model.Add(z[i] + sz[i] <= max_H_mm)

    # No-overlap constraints
    for i in range(n):
        for j in range(i + 1, n):
            bix = model.NewBoolVar(f"ix_{i}_{j}")
            biy = model.NewBoolVar(f"iy_{i}_{j}")
            biz = model.NewBoolVar(f"iz_{i}_{j}")
            bjx = model.NewBoolVar(f"jx_{i}_{j}")
            bjy = model.NewBoolVar(f"jy_{i}_{j}")
            bjz = model.NewBoolVar(f"jz_{i}_{j}")

            model.Add(x[i] + sx[i] <= x[j]).OnlyEnforceIf(bix)
            model.Add(x[j] + sx[j] <= x[i]).OnlyEnforceIf(bjx)
            model.Add(y[i] + sy[i] <= y[j]).OnlyEnforceIf(biy)
            model.Add(y[j] + sy[j] <= y[i]).OnlyEnforceIf(bjy)
            model.Add(z[i] + sz[i] <= z[j]).OnlyEnforceIf(biz)
            model.Add(z[j] + sz[j] <= z[i]).OnlyEnforceIf(bjz)

            model.AddBoolOr([bix, biy, biz, bjx, bjy, bjz])

    # Objective: minimize envelope (constrained within limits)
    maxX = model.NewIntVar(0, max_L_mm, "maxX")
    maxY = model.NewIntVar(0, max_W_mm, "maxY")
    maxZ = model.NewIntVar(0, max_H_mm, "maxZ")
    for i in range(n):
        model.Add(maxX >= x[i] + sx[i])
        model.Add(maxY >= y[i] + sy[i])
        model.Add(maxZ >= z[i] + sz[i])
    model.Minimize(maxX + maxY + maxZ)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = workers

    start = time.time()
    status = solver.Solve(model)
    elapsed_ms = int((time.time() - start) * 1000)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    box_L_cm = max(1, math.ceil(solver.Value(maxX) * MM_TO_CM))
    box_W_cm = max(1, math.ceil(solver.Value(maxY) * MM_TO_CM))
    box_H_cm = max(1, math.ceil(solver.Value(maxZ) * MM_TO_CM))
    box_volume_cm3 = box_L_cm * box_W_cm * box_H_cm

    layout = []
    all_rot_names = ["LWH", "LHW", "WLH", "WHL", "HLW", "HWL"]
    items_volume_cm3 = 0

    for i in range(n):
        px = round(solver.Value(x[i]) * MM_TO_CM, 1)
        py = round(solver.Value(y[i]) * MM_TO_CM, 1)
        pz = round(solver.Value(z[i]) * MM_TO_CM, 1)
        psx = round(solver.Value(sx[i]) * MM_TO_CM, 1)
        psy = round(solver.Value(sy[i]) * MM_TO_CM, 1)
        psz = round(solver.Value(sz[i]) * MM_TO_CM, 1)

        orig_dims = [items[i]["length_cm"], items[i]["width_cm"], items[i]["height_cm"]]
        rot_name = "unknown"
        for k, perm in enumerate(all_perms):
            expected = [orig_dims[perm[0]], orig_dims[perm[1]], orig_dims[perm[2]]]
            if (abs(psx - expected[0]) < 0.2 and
                abs(psy - expected[1]) < 0.2 and
                abs(psz - expected[2]) < 0.2):
                rot_name = all_rot_names[k]
                break

        item_vol = items[i]["length_cm"] * items[i]["width_cm"] * items[i]["height_cm"]
        items_volume_cm3 += item_vol

        layout.append({
            "sku": items[i]["sku"],
            "position": {"x": px, "y": py, "z": pz},
            "placed_dims": {"length": psx, "width": psy, "height": psz},
            "rotation": rot_name,
            "original_dims": {
                "length": items[i]["length_cm"],
                "width": items[i]["width_cm"],
                "height": items[i]["height_cm"],
            },
            "weight_kg": items[i].get("weight_kg", 0),
        })

    utilization = round(items_volume_cm3 / box_volume_cm3, 4) if box_volume_cm3 > 0 else 0

    return {
        "success": True,
        "box": {"length_cm": box_L_cm, "width_cm": box_W_cm, "height_cm": box_H_cm},
        "layout": layout,
        "utilization": utilization,
        "solve_time_ms": elapsed_ms,
        "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
    }
