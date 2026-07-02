"""Performance benchmark tests for packing-api.

Measures execution time for the packing pipeline under various load scenarios.
Run separately from unit tests: python tests/benchmark.py

D5 acceptance criteria:
  - N=1-5: <500ms (engine)
  - N=3: <200ms (engine)
  - Full pipeline (grouper+engine+verifier): <1s for N<=5
  - API response time: <2s for N<=5
"""

from __future__ import annotations

import sys
import os
import time
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine import calculate_min_box, calculate_min_box_with_limits
from app.core.verifier import verify_layout, cross_check_engine_result
from app.services.grouper import apply_group_rules, validate_groups
from app.services.packer import pack_items


# ---------------------------------------------------------------------------
# Helper: generate test items
# ---------------------------------------------------------------------------

def _make_items(n: int, size_range=(5, 40)) -> list[dict]:
    """Generate N test items with random-ish dimensions."""
    items = []
    for i in range(n):
        L = size_range[0] + (i * (size_range[1] - size_range[0]) // n)
        W = L * 0.7
        H = L * 0.5
        items.append({
            "sku": f"ITEM-{i + 1}",
            "length_cm": L,
            "width_cm": round(W, 1),
            "height_cm": round(H, 1),
            "weight_kg": round(L * 0.1, 1),
        })
    return items


def _timed_run(label: str, func, *args, **kwargs) -> tuple[float, any]:
    """Run a function and return elapsed seconds + result."""
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return elapsed, result


# ---------------------------------------------------------------------------
# Benchmark: Engine (calculate_min_box)
# ---------------------------------------------------------------------------

def benchmark_engine():
    """Benchmark OR-Tools engine for increasing item counts."""
    print("\n" + "=" * 60)
    print("BENCHMARK: Engine (calculate_min_box)")
    print("=" * 60)

    results = []
    for n in range(1, 8):
        items = _make_items(n)
        elapsed, result = _timed_run(
            f"N={n}", calculate_min_box, items, time_limit_s=10
        )

        if result:
            box = result["box"]
            solve_ms = result["solve_time_ms"]
            util = result["utilization"]
            status = result.get("status", "?")
            print(
                f"  N={n}: {elapsed * 1000:.0f}ms wall / {solve_ms}ms solve "
                f"| box={box['length_cm']}×{box['width_cm']}×{box['height_cm']} "
                f"| util={util:.2%} | status={status}"
            )
            results.append({"n": n, "wall_ms": elapsed * 1000, "solve_ms": solve_ms})
        else:
            print(f"  N={n}: INFEASIBLE ({elapsed * 1000:.0f}ms)")
            results.append({"n": n, "wall_ms": elapsed * 1000, "solve_ms": 0, "infeasible": True})

    # Check acceptance criteria
    print("\nAcceptance check:")
    for r in results:
        n = r["n"]
        if n <= 5:
            threshold = 500 if n == 5 else 200 if n == 3 else 1000
            wall = r["wall_ms"]
            ok = "PASS" if wall < threshold else "FAIL"
            print(f"  N={n}: {wall:.0f}ms < {threshold}ms → {ok}")

    return results


# ---------------------------------------------------------------------------
# Benchmark: Engine with shipping limits (路B)
# ---------------------------------------------------------------------------

def benchmark_engine_with_limits():
    """Benchmark engine with shipping constraint enforcement."""
    print("\n" + "=" * 60)
    print("BENCHMARK: Engine with shipping limits (路B)")
    print("=" * 60)

    shipping_limits = {
        "max_length_cm": 60,
        "max_width_cm": 40,
        "max_height_cm": 30,
    }

    for n in [3, 5]:
        items = _make_items(n, size_range=(10, 30))
        elapsed, result = _timed_run(
            f"N={n}+limits", calculate_min_box_with_limits,
            items, shipping_limits=shipping_limits, time_limit_s=10
        )

        if result:
            exceeds = result.get("exceeds_limits", False)
            print(
                f"  N={n}: {elapsed * 1000:.0f}ms | "
                f"exceeds_limits={exceeds} | "
                f"box={result['box']} | util={result['utilization']:.2%}"
            )
        else:
            print(f"  N={n}: INFEASIBLE within limits ({elapsed * 1000:.0f}ms)")


# ---------------------------------------------------------------------------
# Benchmark: Verifier (py3dbp)
# ---------------------------------------------------------------------------

def benchmark_verifier():
    """Benchmark py3dbp layout verification."""
    print("\n" + "=" * 60)
    print("BENCHMARK: Verifier (py3dbp)")
    print("=" * 60)

    for n in [3, 5]:
        items = _make_items(n, size_range=(5, 20))
        # First solve with engine to get box dims
        engine_result = calculate_min_box(items, time_limit_s=10)
        if not engine_result:
            print(f"  N={n}: Engine failed, skip verifier")
            continue

        box = engine_result["box"]
        elapsed, verify_result = _timed_run(
            f"N={n}", verify_layout, box, items
        )

        valid = verify_result["valid"]
        unplaced = len(verify_result["unplaced"])
        util = verify_result["utilization"]
        print(
            f"  N={n}: {elapsed * 1000:.0f}ms | "
            f"valid={valid} | unplaced={unplaced} | util={util:.2%}"
        )


# ---------------------------------------------------------------------------
# Benchmark: Full pipeline (packer)
# ---------------------------------------------------------------------------

def benchmark_full_pipeline():
    """Benchmark the complete packer pipeline: grouper + engine + verifier."""
    print("\n" + "=" * 60)
    print("BENCHMARK: Full pipeline (pack_items)")
    print("=" * 60)

    # Scenario 1: Simple — 3 items, no rules
    items = _make_items(3)
    elapsed, result = _timed_run("3 items, no rules", pack_items, items)
    print(f"  3 items (no rules): {elapsed * 1000:.0f}ms | strategy={result['strategy']}")

    # Scenario 2: 5 items with must_pack rules
    items = _make_items(5)
    rules = [
        {"rule_type": "must_pack_together", "source_sku": "ITEM-1", "target_sku": "ITEM-2", "priority": 1},
    ]
    elapsed, result = _timed_run(
        "5 items + must_pack", pack_items, items, group_rules=rules, verify=False
    )
    print(
        f"  5 items (must_pack): {elapsed * 1000:.0f}ms | "
        f"strategy={result['strategy']} | boxes={result['total_boxes']}"
    )

    # Scenario 3: 5 items with must_not + shipping limits
    items = _make_items(5, size_range=(5, 20))
    rules = [
        {"rule_type": "must_not_pack_together", "source_sku": "ITEM-1", "target_sku": "ITEM-5", "priority": 1},
    ]
    shipping_limits = {"max_weight_kg": 10}
    elapsed, result = _timed_run(
        "5 items + must_not + weight_limit",
        pack_items, items, group_rules=rules, shipping_limits=shipping_limits, verify=False
    )
    print(
        f"  5 items (must_not+weight): {elapsed * 1000:.0f}ms | "
        f"strategy={result['strategy']} | boxes={result['total_boxes']}"
    )

    # Acceptance check
    print("\nPipeline acceptance check (<1s for N<=5):")
    print(f"  All scenarios should be <1000ms")


# ---------------------------------------------------------------------------
# Benchmark: Grouper
# ---------------------------------------------------------------------------

def benchmark_grouper():
    """Benchmark the grouper constraint layer."""
    print("\n" + "=" * 60)
    print("BENCHMARK: Grouper (apply_group_rules)")
    print("=" * 60)

    items = _make_items(5)
    rules = [
        {"rule_type": "must_pack_together", "source_sku": "ITEM-1", "target_sku": "ITEM-2", "priority": 1},
        {"rule_type": "must_not_pack_together", "source_sku": "ITEM-3", "target_sku": "ITEM-5", "priority": 1},
        {"rule_type": "pack_near", "source_sku": "ITEM-1", "target_sku": "ITEM-4", "priority": 2},
    ]

    elapsed, groups = _timed_run("5 items + 3 rules", apply_group_rules, items, rules)
    print(f"  5 items + 3 rules: {elapsed * 1000:.1f}ms | {len(groups)} groups")

    # Validate
    elapsed_v, validation = _timed_run("validate", validate_groups, groups, rules)
    print(f"  Validate: {elapsed_v * 1000:.1f}ms | valid={validation['valid']}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main():
    """Run all benchmark scenarios."""
    print("=" * 60)
    print("PACKING-API PERFORMANCE BENCHMARKS")
    print("=" * 60)

    all_results = {}
    all_results["engine"] = benchmark_engine()
    benchmark_engine_with_limits()
    benchmark_verifier()
    benchmark_grouper()
    benchmark_full_pipeline()

    print("\n" + "=" * 60)
    print("BENCHMARKS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
