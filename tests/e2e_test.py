"""End-to-end integration tests (D10: W2 validation).

Simulates the full workflow from items input through packing,
grouping, shipping comparison, and dual-path recommendation.
Validates the complete API pipeline end-to-end.

W2 acceptance criteria:
  - Full pipeline: grouper → engine → verifier → shipping → recommendation
  - Dual-path comparison works correctly
  - All surcharge rules trigger when expected
  - API returns complete, consistent response
  - Performance: full pipeline <2s for N<=5
"""

import pytest
import time
from fastapi.testclient import TestClient

from app.main import app
from app.services.packer import pack_items
from app.services.grouper import apply_group_rules, validate_groups
from app.services.shipping import (
    calculate_dim_weight_cm,
    calculate_billable_weight,
    get_shipping_recommendation,
)
from app.core.engine import calculate_min_box


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _item(sku, L, W, H, weight=0.5, fragile=False, heavy=False):
    return {
        "sku": sku, "length_cm": L, "width_cm": W,
        "height_cm": H, "weight_kg": weight,
        "fragile": fragile, "heavy": heavy,
    }


client = TestClient(app)


# ---------------------------------------------------------------------------
# Full pipeline via pack_items service
# ---------------------------------------------------------------------------

class TestFullPipelinePackItems:
    """E2E tests via pack_items service layer."""

    def test_simple_3_items_full_pipeline(self):
        """3 items, no rules → single box + shipping comparison."""
        items = [
            _item("HOST", 40, 30, 20, 8),
            _item("ACC", 15, 10, 5, 0.8),
            _item("CABLE", 5, 5, 3, 0.2),
        ]
        result = pack_items(items, destination=6, verify=True)

        assert result["strategy"] == "single_box"
        assert len(result["groups"]) == 1
        g = result["groups"][0]
        assert g["box"] is not None
        assert g["utilization"] > 0
        assert g["solve_time_ms"] < 500
        assert g["shipping"] is not None
        assert g["shipping"]["recommended"] is not None
        assert len(g["shipping"]["all_carriers"]) == 3

    def test_must_not_pack_splits_into_groups(self):
        """must_not_pack → multiple groups → multi_box."""
        items = [
            _item("FRAG", 10, 10, 10, 0.5, fragile=True),
            _item("HEAVY", 30, 20, 15, 50, heavy=True),
        ]
        rules = [
            {"rule_type": "must_not_pack_together", "source_sku": "FRAG",
             "target_sku": "HEAVY", "priority": 1},
        ]
        result = pack_items(items, group_rules=rules, destination=6, verify=False)

        assert result["strategy"] == "multi_box"
        assert len(result["groups"]) == 2
        # Each group should have shipping
        for g in result["groups"]:
            assert g["shipping"] is not None

    def test_must_pack_keeps_items_in_same_box(self):
        """must_pack_together → items stay in same box."""
        items = [
            _item("HOST", 40, 30, 20, 8),
            _item("ACC", 15, 10, 5, 0.8),
        ]
        rules = [
            {"rule_type": "must_pack_together", "source_sku": "HOST",
             "target_sku": "ACC", "priority": 1},
        ]
        result = pack_items(items, group_rules=rules, destination=6, verify=False)

        assert len(result["groups"]) == 1
        skus = [it["sku"] for it in result["groups"][0]["items"]]
        assert "HOST" in skus and "ACC" in skus

    def test_weight_split_creates_multiple_boxes(self):
        """P0 weight limit → items split into multiple boxes."""
        items = [
            _item("A", 30, 20, 15, 25),
            _item("B", 25, 18, 12, 20),
            _item("C", 10, 8, 5, 2),
        ]
        limits = {"max_weight_kg": 30}

        result = pack_items(
            items, shipping_limits=limits, destination=6, verify=False
        )

        assert result["strategy"] == "multi_box"
        # Each group weight should be under 30kg (or single item exceeding)
        for g in result["groups"]:
            total_wt = sum(it["weight_kg"] for it in g["items"])
            assert total_wt <= 30 or len(g["items"]) == 1

    def test_full_pipeline_performance(self):
        """Full pipeline for 5 items should complete within 2 seconds."""
        items = [
            _item("I1", 40, 30, 20, 5),
            _item("I2", 15, 10, 5, 0.8),
            _item("I3", 12, 8, 6, 0.5),
            _item("I4", 8, 6, 4, 0.3),
            _item("I5", 5, 5, 3, 0.2),
        ]
        rules = [
            {"rule_type": "must_pack_together", "source_sku": "I1",
             "target_sku": "I2", "priority": 1},
            {"rule_type": "pack_near", "source_sku": "I1",
             "target_sku": "I3", "priority": 2},
        ]

        start = time.time()
        result = pack_items(
            items, group_rules=rules, destination=6,
            verify=False, time_limit_s=5,
        )
        elapsed = time.time() - start

        assert elapsed < 2.0, f"Pipeline took {elapsed:.2f}s, exceeds 2s threshold"
        assert result["strategy"] in ("single_box", "multi_box")


# ---------------------------------------------------------------------------
# Full pipeline via API
# ---------------------------------------------------------------------------

class TestFullPipelineAPI:
    """E2E tests via /pack/direct API endpoint."""

    def test_api_single_box_with_shipping(self):
        """API: single box with full shipping comparison."""
        payload = {
            "items": [
                {"sku": "BOX-1", "length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 5},
            ],
            "destination": "6",
            "verify": False,
        }
        resp = client.post("/pack/direct", json=payload)
        assert resp.status_code == 200

        result = resp.json()
        assert result["strategy"] == "single_box"
        g = result["groups"][0]
        assert g["box"] is not None
        assert g["shipping"]["recommended"]["carrier"] is not None

    def test_api_dual_path_comparison(self):
        """API: dual-path comparison with shipping limits."""
        payload = {
            "items": [
                {"sku": "HOST", "length_cm": 40, "width_cm": 30, "height_cm": 20, "weight_kg": 8},
                {"sku": "ACC", "length_cm": 15, "width_cm": 10, "height_cm": 5, "weight_kg": 0.8},
            ],
            "shipping_limits": {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30},
            "destination": "6",
            "dual_path": True,
            "verify": False,
        }
        resp = client.post("/pack/direct", json=payload)
        assert resp.status_code == 200

        result = resp.json()
        g = result["groups"][0]
        dp = g["dual_path"]
        assert dp["recommendation"] is not None
        assert dp["cost_comparison"]["path_a_cost_usd"] is not None

    def test_api_exceeds_limits_dual_path(self):
        """API: items exceeding limits → path_b_only."""
        payload = {
            "items": [
                {"sku": "BIG", "length_cm": 130, "width_cm": 40, "height_cm": 30, "weight_kg": 15},
            ],
            "shipping_limits": {"max_length_cm": 120, "max_width_cm": 80, "max_height_cm": 60},
            "destination": "intl",
            "dual_path": True,
            "verify": False,
        }
        resp = client.post("/pack/direct", json=payload)
        assert resp.status_code == 200

        result = resp.json()
        g = result["groups"][0]
        assert g["dual_path"]["recommendation"] == "path_b_only"

    def test_api_group_rules_with_shipping(self):
        """API: group rules + shipping comparison."""
        payload = {
            "items": [
                {"sku": "FRAG", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": 0.5, "fragile": True},
                {"sku": "HEAVY", "length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 50, "heavy": True},
            ],
            "group_rules": [
                {"rule_type": "must_not_pack_together", "source_sku": "FRAG", "target_sku": "HEAVY", "priority": 1},
            ],
            "destination": "6",
            "verify": False,
        }
        resp = client.post("/pack/direct", json=payload)
        assert resp.status_code == 200

        result = resp.json()
        assert result["strategy"] == "multi_box"
        assert len(result["groups"]) == 2
        # Each group should have shipping recommendation
        for g in result["groups"]:
            if g["box"]:
                assert g["shipping"] is not None

    def test_api_response_consistency(self):
        """API: all response fields are consistent and non-contradictory."""
        payload = {
            "items": [
                {"sku": "A", "length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 5},
                {"sku": "B", "length_cm": 15, "width_cm": 10, "height_cm": 8, "weight_kg": 2},
            ],
            "destination": "6",
            "verify": False,
        }
        resp = client.post("/pack/direct", json=payload)
        result = resp.json()

        # Strategy matches actual group count
        total_boxes = result["total_boxes"]
        feasible = result["feasible_boxes"]
        if total_boxes == 1 and feasible == 1:
            assert result["strategy"] == "single_box"
        elif total_boxes > 1:
            assert result["strategy"] == "multi_box"

        # Utilization is between 0 and 1
        assert 0 <= result["total_utilization"] <= 1

        # All SKUs appear in groups
        all_skus = []
        for g in result["groups"]:
            all_skus.extend(it["sku"] for it in g["items"])
        assert set(all_skus) == {"A", "B"}

    def test_api_empty_items(self):
        """API: empty items list → strategy 'empty'."""
        payload = {
            "items": [],
            "destination": "6",
        }
        resp = client.post("/pack/direct", json=payload)
        # This should fail validation (min_length=1)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Cross-module integration tests
# ---------------------------------------------------------------------------

class TestCrossModuleIntegration:
    """Tests that validate interactions between multiple modules."""

    def test_engine_box_matches_shipping_dim_weight(self):
        """Engine box dims → shipping dim_weight should be consistent."""
        items = [_item("A", 60, 40, 30, 10)]
        engine_result = calculate_min_box(items, time_limit_s=5)
        assert engine_result is not None

        box = engine_result["box"]
        dim_weight = calculate_dim_weight_cm(
            box["length_cm"], box["width_cm"], box["height_cm"]
        )

        # Shipping recommendation should use the same box dims
        shipping = get_shipping_recommendation(box, 10, "intl")
        # DHL should use metric dim_weight
        dhl = next(c for c in shipping["all_carriers"] if c["carrier"] == "DHL")
        assert dhl["dim_weight_kg"] == round(dim_weight, 2)

    def test_grouper_output_feeds_engine_correctly(self):
        """Grouper output groups → each fed to engine independently."""
        items = [
            _item("FRAG", 10, 10, 10, 0.5),
            _item("HEAVY", 30, 20, 15, 50),
        ]
        rules = [
            {"rule_type": "must_not_pack_together", "source_sku": "FRAG",
             "target_sku": "HEAVY", "priority": 1},
        ]
        groups = apply_group_rules(items, rules)

        # Each group should be solvable independently
        for g in groups:
            result = calculate_min_box(g, time_limit_s=5)
            assert result is not None
            assert result["success"] is True

    def test_grouper_validation_matches_result(self):
        """validate_groups should confirm pack_items grouping results."""
        items = [
            _item("A", 30, 20, 15, 5),
            _item("B", 15, 10, 8, 2),
        ]
        rules = [
            {"rule_type": "must_pack_together", "source_sku": "A",
             "target_sku": "B", "priority": 1},
        ]
        result = pack_items(items, group_rules=rules, destination=6, verify=False)

        # Group validation should be valid
        gv = result["group_validation"]
        assert gv["valid"] is True
        assert len(gv["violations"]) == 0

    def test_shipping_surcharges_for_exceeding_box(self):
        """Shipping surcharges trigger correctly for oversized box."""
        # 130cm > 120cm DHL Oversize trigger
        box = {"length_cm": 130, "width_cm": 40, "height_cm": 30}
        shipping = get_shipping_recommendation(box, 15, "intl")

        # DHL should have Oversize surcharge
        dhl = next(c for c in shipping["all_carriers"] if c["carrier"] == "DHL")
        oversize = [s for s in dhl["surcharges"] if s["trigger_type"] == "oversize"]
        assert len(oversize) >= 1

    def test_weight_limit_split_then_shipping_per_group(self):
        """P0 weight split → each group gets independent shipping calc."""
        items = [
            _item("A", 30, 20, 15, 25),
            _item("B", 25, 18, 12, 20),
            _item("C", 10, 8, 5, 2),
        ]
        limits = {"max_weight_kg": 30}
        result = pack_items(
            items, shipping_limits=limits, destination=6, verify=False
        )

        # Each group should have its own shipping recommendation
        for g in result["groups"]:
            if g["box"]:
                assert g["shipping"] is not None
                assert g["shipping"]["recommended"] is not None

    def test_dual_path_plus_group_rules_plus_weight_limit(self):
        """Triple combo: group rules + weight limit + dual-path."""
        items = [
            _item("HOST", 40, 30, 20, 8),
            _item("ACC", 15, 10, 5, 0.8),
            _item("FRAG", 10, 10, 10, 0.5, fragile=True),
            _item("HEAVY", 30, 25, 15, 25, heavy=True),
        ]
        rules = [
            {"rule_type": "must_pack_together", "source_sku": "HOST",
             "target_sku": "ACC", "priority": 1},
            {"rule_type": "must_not_pack_together", "source_sku": "FRAG",
             "target_sku": "HEAVY", "priority": 1},
        ]
        limits = {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30,
                  "max_weight_kg": 30}

        result = pack_items(
            items, group_rules=rules, shipping_limits=limits,
            destination=6, dual_path=True, verify=False,
        )

        # Should have groups, each with shipping and dual_path data
        assert len(result["groups"]) >= 2
        for g in result["groups"]:
            if g["box"]:
                assert g["shipping"] is not None
                assert "dual_path" in g
