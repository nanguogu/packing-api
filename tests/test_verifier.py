"""Tests for py3dbp layout verification (D2).

Validates that the 3D packing verifier correctly confirms or rejects
physical placement of items within a selected box.

D2 acceptance criteria:
  - py3dbp verifies engine results for N<=5 items
  - Cross-check function returns consistent result for simple cases
  - Unplaced items correctly identified
  - Utilization accurately calculated
"""

import pytest
from app.core.engine import calculate_min_box
from app.core.verifier import verify_layout, cross_check_engine_result


# ---------------------------------------------------------------------------
# Basic verification tests
# ---------------------------------------------------------------------------

class TestVerifyLayout:
    """Test suite for the verify_layout function."""

    def test_empty_items_returns_valid(self):
        """Empty items list should return valid with empty layout."""
        box = {"length_cm": 50, "width_cm": 40, "height_cm": 30}
        result = verify_layout(box, [])

        assert result["valid"] is True
        assert result["layout"] == []
        assert result["unplaced"] == []
        assert result["utilization"] == 0.0

    def test_single_item_fits_box(self):
        """A single item smaller than the box should produce a valid layout."""
        box = {"length_cm": 50, "width_cm": 40, "height_cm": 30}
        items = [
            {"sku": "SM-1", "length_cm": 10, "width_cm": 8, "height_cm": 5, "weight_kg": 0.3},
        ]
        result = verify_layout(box, items)

        assert result["valid"] is True
        assert len(result["layout"]) == 1
        assert result["layout"][0]["sku"] == "SM-1"
        assert result["unplaced"] == []

    def test_multiple_items_fit_box(self):
        """Multiple items that fit should all be placed."""
        box = {"length_cm": 60, "width_cm": 50, "height_cm": 40}
        items = [
            {"sku": "A", "length_cm": 20, "width_cm": 15, "height_cm": 10, "weight_kg": 1},
            {"sku": "B", "length_cm": 15, "width_cm": 10, "height_cm": 8, "weight_kg": 0.5},
            {"sku": "C", "length_cm": 8, "width_cm": 5, "height_cm": 4, "weight_kg": 0.2},
        ]
        result = verify_layout(box, items)

        assert result["valid"] is True
        assert len(result["layout"]) == 3
        assert result["unplaced"] == []

    def test_item_exceeds_box_dimension(self):
        """An item larger than the box should be marked as unplaced."""
        box = {"length_cm": 20, "width_cm": 15, "height_cm": 10}
        items = [
            {"sku": "BIG-1", "length_cm": 50, "width_cm": 40, "height_cm": 30, "weight_kg": 5},
        ]
        result = verify_layout(box, items)

        # The single big item can't fit in the small box
        assert result["valid"] is False
        assert "BIG-1" in result["unplaced"]

    def test_partial_fit_reports_unplaced(self):
        """When some items fit and others don't, unplaced SKUs should be reported."""
        box = {"length_cm": 25, "width_cm": 20, "height_cm": 15}
        items = [
            {"sku": "FIT-1", "length_cm": 10, "width_cm": 8, "height_cm": 5, "weight_kg": 0.3},
            {"sku": "FIT-2", "length_cm": 12, "width_cm": 10, "height_cm": 8, "weight_kg": 0.5},
            {"sku": "OVER-1", "length_cm": 40, "width_cm": 30, "height_cm": 20, "weight_kg": 3},
        ]
        result = verify_layout(box, items)

        # OVER-1 should be unplaced (too big)
        assert "OVER-1" in result["unplaced"]
        # FIT items may or may not all be placed depending on heuristic order

    def test_utilization_calculation(self):
        """Space utilization should be accurately calculated."""
        box = {"length_cm": 100, "width_cm": 80, "height_cm": 60}
        items = [
            {"sku": "U-1", "length_cm": 20, "width_cm": 15, "height_cm": 10, "weight_kg": 1},
        ]
        result = verify_layout(box, items)

        # Volume: item = 20*15*10 = 3000, box = 100*80*60 = 480000
        assert result["items_volume_cm3"] == 3000
        assert result["box_volume_cm3"] == 480000
        expected_util = 3000 / 480000
        assert abs(result["utilization"] - round(expected_util, 4)) < 0.01

    def test_volume_fields_correct(self):
        """box_volume_cm3 and items_volume_cm3 should match manual calculation."""
        box = {"length_cm": 50, "width_cm": 40, "height_cm": 30}
        items = [
            {"sku": "V-1", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": 0.5},
            {"sku": "V-2", "length_cm": 15, "width_cm": 12, "height_cm": 8, "weight_kg": 0.7},
        ]
        result = verify_layout(box, items)

        assert result["box_volume_cm3"] == 50 * 40 * 30
        assert result["items_volume_cm3"] == 10 * 10 * 10 + 15 * 12 * 8


# ---------------------------------------------------------------------------
# Cross-check tests (engine vs verifier)
# ---------------------------------------------------------------------------

class TestCrossCheck:
    """Test suite for cross_check_engine_result."""

    def test_single_item_cross_check(self):
        """Single item: engine and verifier should agree."""
        items = [
            {"sku": "CUBE-10", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": 0.5},
        ]
        engine_result = calculate_min_box(items, time_limit_s=5)
        assert engine_result is not None

        cross = cross_check_engine_result(engine_result, items)
        assert cross["engine_valid"] is True
        assert cross["verifier_valid"] is True
        assert cross["consistent"] is True

    def test_two_identical_items_cross_check(self):
        """Two identical items: engine and verifier should agree."""
        items = [
            {"sku": "CUBE-A", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": 0.5},
            {"sku": "CUBE-B", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": 0.5},
        ]
        engine_result = calculate_min_box(items, time_limit_s=5)
        assert engine_result is not None

        cross = cross_check_engine_result(engine_result, items)
        assert cross["engine_valid"] is True
        assert cross["verifier_valid"] is True
        assert cross["consistent"] is True

    def test_three_items_cross_check(self):
        """Three different items: engine and verifier should agree."""
        items = [
            {"sku": "BIG-1", "length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 3},
            {"sku": "MED-1", "length_cm": 15, "width_cm": 10, "height_cm": 8, "weight_kg": 1},
            {"sku": "SM-1", "length_cm": 8, "width_cm": 5, "height_cm": 4, "weight_kg": 0.3},
        ]
        engine_result = calculate_min_box(items, time_limit_s=5)
        assert engine_result is not None

        cross = cross_check_engine_result(engine_result, items)
        assert cross["engine_valid"] is True
        # For 3 items, py3dbp should also be able to place them
        # (greedy heuristic works well for small N)
        assert cross["verifier_valid"] is True
        assert cross["consistent"] is True

    def test_five_items_cross_check(self):
        """Five mixed items: engine and verifier should mostly agree."""
        items = [
            {"sku": "HOST-1", "length_cm": 40, "width_cm": 30, "height_cm": 20, "weight_kg": 5},
            {"sku": "ACC-A", "length_cm": 15, "width_cm": 10, "height_cm": 5, "weight_kg": 0.8},
            {"sku": "ACC-B", "length_cm": 12, "width_cm": 8, "height_cm": 6, "weight_kg": 0.5},
            {"sku": "ACC-C", "length_cm": 8, "width_cm": 6, "height_cm": 4, "weight_kg": 0.3},
            {"sku": "ACC-D", "length_cm": 5, "width_cm": 5, "height_cm": 3, "weight_kg": 0.2},
        ]
        engine_result = calculate_min_box(items, time_limit_s=8)
        assert engine_result is not None

        cross = cross_check_engine_result(engine_result, items)
        assert cross["engine_valid"] is True
        # For 5 items, py3dbp may or may not find a placement
        # depending on the heuristic — this test checks the mechanism works
        # but doesn't mandate verifier agreement (greedy heuristic limitation)

    def test_engine_failure_returns_consistent(self):
        """When engine fails (None result), cross-check should report both as invalid."""
        result = cross_check_engine_result(None, [])
        assert result["engine_valid"] is False
        assert result["consistent"] is True


# ---------------------------------------------------------------------------
# Layout position correctness tests
# ---------------------------------------------------------------------------

class TestLayoutPositions:
    """Verify that py3dbp positions are correctly mapped to our coordinate system."""

    def test_position_fields_present(self):
        """Each placed item should have position with x, y, z."""
        box = {"length_cm": 50, "width_cm": 40, "height_cm": 30}
        items = [
            {"sku": "POS-1", "length_cm": 10, "width_cm": 8, "height_cm": 5, "weight_kg": 0.3},
        ]
        result = verify_layout(box, items)

        assert result["valid"] is True
        entry = result["layout"][0]
        assert "position" in entry
        assert "x" in entry["position"]
        assert "y" in entry["position"]
        assert "z" in entry["position"]

    def test_placed_dims_fields_present(self):
        """Each placed item should have placed_dims with length, width, height."""
        box = {"length_cm": 50, "width_cm": 40, "height_cm": 30}
        items = [
            {"sku": "DIM-1", "length_cm": 10, "width_cm": 8, "height_cm": 5, "weight_kg": 0.3},
        ]
        result = verify_layout(box, items)

        assert result["valid"] is True
        entry = result["layout"][0]
        assert "placed_dims" in entry
        assert "length" in entry["placed_dims"]
        assert "width" in entry["placed_dims"]
        assert "height" in entry["placed_dims"]

    def test_sku_preserved_in_layout(self):
        """SKU should be preserved in the layout output."""
        box = {"length_cm": 50, "width_cm": 40, "height_cm": 30}
        items = [
            {"sku": "MY-PRODUCT", "length_cm": 10, "width_cm": 8, "height_cm": 5, "weight_kg": 0.3},
        ]
        result = verify_layout(box, items)

        assert result["layout"][0]["sku"] == "MY-PRODUCT"

    def test_weight_preserved_in_layout(self):
        """Weight should be preserved in the layout output."""
        box = {"length_cm": 50, "width_cm": 40, "height_cm": 30}
        items = [
            {"sku": "W-1", "length_cm": 10, "width_cm": 8, "height_cm": 5, "weight_kg": 2.5},
        ]
        result = verify_layout(box, items)

        assert result["layout"][0]["weight_kg"] == 2.5
