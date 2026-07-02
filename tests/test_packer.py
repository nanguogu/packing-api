"""Tests for packer service layer (D9: dual-path comparison).

Validates that the dual-path comparison correctly:
  1. Computes both Path A (compliant) and Path B (optimal minimum)
  2. Compares total shipping costs and recommends cheapest
  3. Handles cases where items can't fit within limits (Path A infeasible)
  4. Handles cases where Path B is cheaper despite surcharges
  5. Handles cases where Path A is cheaper (compliant, no surcharges)
"""

import pytest
from app.services.packer import pack_items, _compute_dual_path
from app.core.engine import calculate_min_box, calculate_min_box_with_limits


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _item(sku, L, W, H, weight=0.5):
    return {"sku": sku, "length_cm": L, "width_cm": W, "height_cm": H, "weight_kg": weight}


# ---------------------------------------------------------------------------
# _compute_dual_path tests
# ---------------------------------------------------------------------------

class TestComputeDualPath:
    """Test the dual-path comparison engine."""

    def test_items_fit_within_limits_both_paths(self):
        """Items that fit within limits → both paths feasible, recommend cheapest."""
        items = [
            _item("A", 40, 30, 20, 8),
            _item("B", 15, 10, 5, 0.8),
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30}

        result = _compute_dual_path(items, limits, destination=6, time_limit_s=5, workers=4)

        # Path A should be feasible (box fits within limits)
        assert result["path_a"] is not None
        assert result["path_a"]["box"] is not None

        # Path B should be feasible
        assert result["path_b"] is not None
        assert result["path_b"]["box"] is not None

        # Both should have shipping costs
        assert result["cost_comparison"]["path_a_cost_usd"] is not None
        assert result["cost_comparison"]["path_b_cost_usd"] is not None

    def test_items_exceed_limits_path_a_infeasible(self):
        """Items exceeding limits → Path A infeasible, only Path B."""
        items = [
            _item("BIG", 130, 40, 30, 15),
            _item("SM", 10, 8, 5, 0.3),
        ]
        limits = {"max_length_cm": 120, "max_width_cm": 80, "max_height_cm": 60}

        result = _compute_dual_path(items, limits, destination="intl", time_limit_s=5, workers=4)

        # Path A should NOT be feasible (130cm > 120cm limit)
        # recommendation should be path_b_only
        assert result["recommendation"] == "path_b_only"
        assert result["cost_comparison"]["path_a_cost_usd"] is None

    def test_recommendation_is_cheapest(self):
        """Recommendation should choose the path with lowest total shipping cost."""
        items = [
            _item("A", 30, 20, 15, 5),
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30}

        result = _compute_dual_path(items, limits, destination=6, time_limit_s=5, workers=4)

        # Verify recommendation logic
        a_cost = result["cost_comparison"]["path_a_cost_usd"]
        b_cost = result["cost_comparison"]["path_b_cost_usd"]

        if a_cost is not None and b_cost is not None:
            if a_cost <= b_cost:
                assert result["recommendation"] == "path_a"
            else:
                assert result["recommendation"] == "path_b"

    def test_path_a_box_actually_within_limits(self):
        """Verify Path A box dimensions comply with shipping limits."""
        items = [
            _item("A", 40, 30, 20, 8),
            _item("B", 15, 10, 5, 0.8),
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30}

        result = _compute_dual_path(items, limits, destination=6, time_limit_s=5, workers=4)

        if result["path_a"] and result["cost_comparison"]["path_a_cost_usd"] is not None:
            box = result["path_a"]["box"]
            # All dims should be within limits (sorted comparison)
            dims = sorted([box["length_cm"], box["width_cm"], box["height_cm"]])
            max_dims = sorted([50, 40, 30])
            for d, m in zip(dims, max_dims):
                assert d <= m + 1, f"Path A dim {d} exceeds limit {m}"


# ---------------------------------------------------------------------------
# Full pipeline dual-path tests (pack_items with dual_path=True)
# ---------------------------------------------------------------------------

class TestPackItemsDualPath:
    """Test pack_items with dual_path=True parameter."""

    def test_dual_path_flag_in_response(self):
        """When dual_path=True, result should contain dual_path data."""
        items = [
            _item("A", 30, 20, 15, 5),
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30}

        result = pack_items(
            items, shipping_limits=limits, destination=6,
            dual_path=True, verify=False,
        )

        assert len(result["groups"]) >= 1
        group = result["groups"][0]
        assert "dual_path" in group
        assert group["dual_path"]["recommendation"] is not None

    def test_dual_path_false_no_dual_data(self):
        """When dual_path=False, result should NOT contain dual_path data."""
        items = [
            _item("A", 30, 20, 15, 5),
        ]

        result = pack_items(items, verify=False)

        group = result["groups"][0]
        assert "dual_path" not in group

    def test_dual_path_with_group_rules(self):
        """Dual-path should work alongside grouping constraints."""
        items = [
            _item("HOST", 40, 30, 20, 8),
            _item("ACC", 15, 10, 5, 0.8),
            _item("FRAG", 10, 10, 10, 0.5),
        ]
        rules = [
            {"rule_type": "must_pack_together", "source_sku": "HOST", "target_sku": "ACC", "priority": 1},
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30}

        result = pack_items(
            items, group_rules=rules, shipping_limits=limits,
            destination=6, dual_path=True, verify=False,
        )

        # Should have groups with dual_path data
        for g in result["groups"]:
            if g["box"]:
                assert "dual_path" in g

    def test_dual_path_exceeds_limits_correct_handling(self):
        """Items exceeding limits → dual_path should show path_b_only."""
        items = [
            _item("BIG", 130, 40, 30, 15),
        ]
        limits = {"max_length_cm": 120, "max_width_cm": 80, "max_height_cm": 60}

        result = pack_items(
            items, shipping_limits=limits, destination="intl",
            dual_path=True, verify=False,
        )

        group = result["groups"][0]
        dp = group["dual_path"]
        # Path A should not be feasible (130cm > 120cm)
        assert dp["recommendation"] == "path_b_only"

    def test_summary_includes_dual_path_info(self):
        """Summary text should include dual-path comparison results."""
        items = [
            _item("A", 40, 30, 20, 8),
            _item("B", 15, 10, 5, 0.8),
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30}

        result = pack_items(
            items, shipping_limits=limits, destination=6,
            dual_path=True, verify=False,
        )

        # Summary should contain "Dual-path" keyword
        assert "Dual-path" in result["summary"] or "dual" in result["summary"].lower()

    def test_empty_items_dual_path(self):
        """Empty items should work with dual_path=True."""
        result = pack_items([], dual_path=True)
        assert result["strategy"] == "empty"

    def test_savings_reported_when_paths_differ(self):
        """Savings should be reported when paths have different costs."""
        items = [
            _item("A", 40, 30, 20, 8),
            _item("B", 15, 10, 5, 0.8),
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30}

        result = pack_items(
            items, shipping_limits=limits, destination=6,
            dual_path=True, verify=False,
        )

        group = result["groups"][0]
        dp = group["dual_path"]
        cc = dp["cost_comparison"]

        # Both costs should be computed
        assert cc["path_a_cost_usd"] is not None
        assert cc["path_b_cost_usd"] is not None

        # Savings should be reported (even if 0 for same cost)
        if cc["path_a_cost_usd"] != cc["path_b_cost_usd"]:
            assert cc["savings_usd"] is not None
            assert cc["savings_usd"] > 0


# ---------------------------------------------------------------------------
# Integration: dual-path + shipping surcharges
# ---------------------------------------------------------------------------

class TestDualPathWithSurcharges:
    """Test that surcharges correctly affect dual-path cost comparison."""

    def test_path_b_oversize_surcharge_visible(self):
        """Path B with Oversize surcharge should show in dual-path details."""
        items = [
            _item("BIG", 130, 40, 30, 15),
        ]
        limits = {"max_length_cm": 120, "max_width_cm": 80, "max_height_cm": 60}

        result = _compute_dual_path(items, limits, destination="intl", time_limit_s=5, workers=4)

        # Path B should have shipping with DHL Oversize surcharge
        if result["path_b_shipping"] and result["path_b_shipping"]["recommended"]:
            rec = result["path_b_shipping"]["recommended"]
            oversize_found = any(
                s["trigger_type"] == "oversize" for s in rec["surcharges"]
            )
            assert oversize_found, "DHL Oversize surcharge should trigger for 130cm item"

    def test_path_a_no_oversize_surcharge(self):
        """Path A (compliant) should NOT have Oversize surcharge."""
        items = [
            _item("A", 40, 30, 20, 8),
            _item("B", 15, 10, 5, 0.8),
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30}

        result = _compute_dual_path(items, limits, destination=6, time_limit_s=5, workers=4)

        if result["path_a_shipping"] and result["path_a_shipping"]["recommended"]:
            rec = result["path_a_shipping"]["recommended"]
            oversize_found = any(
                s["trigger_type"] == "oversize" for s in rec["surcharges"]
            )
            assert not oversize_found, "Path A should have no Oversize surcharge"
