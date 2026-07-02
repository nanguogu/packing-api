"""D14: Front-end/back-end integration validation.

Since this is a pure backend API (no front-end HTML pages),
D14 validates:
  1. Swagger docs accessible via TestClient
  2. All 5 API endpoints return correct content-type and structure
  3. Full user workflow simulation: items -> pack -> list -> viz
  4. Error handling: invalid input, missing fields
  5. API response consistency across endpoints
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


# ---------------------------------------------------------------------------
# Swagger docs (OpenAPI spec)
# ---------------------------------------------------------------------------

class TestSwaggerDocs:
    """Validate OpenAPI spec is complete and all endpoints documented."""

    def test_openapi_json_accessible(self):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        assert spec["info"]["title"] == "Packing Optimization & Logistics Recommendation API"
        assert spec["info"]["version"] == "0.1.0"

    def test_all_pack_endpoints_documented(self):
        resp = client.get("/openapi.json")
        paths = resp.json()["paths"]
        pack_endpoints = [p for p in paths if p.startswith("/pack")]
        expected = [
            "/pack/",
            "/pack/detail",
            "/pack/direct",
            "/pack/list",
            "/pack/list/text",
            "/pack/viz",
        ]
        for ep in expected:
            assert ep in paths, f"Endpoint {ep} not found in OpenAPI spec"

    def test_product_endpoints_documented(self):
        resp = client.get("/openapi.json")
        paths = resp.json()["paths"]
        product_endpoints = [p for p in paths if p.startswith("/products")]
        assert len(product_endpoints) > 0

    def test_schema_definitions_complete(self):
        resp = client.get("/openapi.json")
        schemas = resp.json()["components"]["schemas"]
        expected_schemas = [
            "PackDirectRequest",
            "PackItem",
            "GroupRuleInput",
            "ShippingLimits",
        ]
        for s in expected_schemas:
            assert s in schemas, f"Schema {s} not found in OpenAPI spec"


# ---------------------------------------------------------------------------
# Full user workflow simulation
# ---------------------------------------------------------------------------

class TestUserWorkflow:
    """Simulate complete user journey through the API."""

    def test_workflow_pack_direct_to_list_to_viz(self):
        """User: pack items -> get packing list -> view 3D viz."""
        payload = {
            "items": [
                {"sku": "HOST", "length_cm": 40, "width_cm": 30, "height_cm": 20, "weight_kg": 8},
                {"sku": "ACC", "length_cm": 15, "width_cm": 10, "height_cm": 5, "weight_kg": 0.8},
                {"sku": "CABLE", "length_cm": 5, "width_cm": 5, "height_cm": 3, "weight_kg": 0.2},
            ],
            "destination": "6",
            "verify": False,
        }

        # Step 1: Pack directly
        resp_pack = client.post("/pack/direct", json=payload)
        assert resp_pack.status_code == 200
        pack_result = resp_pack.json()
        assert pack_result["strategy"] == "single_box"
        assert len(pack_result["groups"]) == 1

        # Step 2: Get packing list
        resp_list = client.post("/pack/list", json=payload)
        assert resp_list.status_code == 200
        list_result = resp_list.json()
        assert len(list_result["boxes"]) == 1
        assert list_result["boxes"][0]["item_count"] == 3

        # Step 3: Get text packing list
        resp_text = client.post("/pack/list/text", json=payload)
        assert resp_text.status_code == 200
        text_result = resp_text.json()
        assert "PACKING LIST" in text_result["text"]
        assert "HOST" in text_result["text"]

        # Step 4: Get 3D visualization
        resp_viz = client.post("/pack/viz", json=payload)
        assert resp_viz.status_code == 200
        assert "text/html" in resp_viz.headers.get("content-type", "")
        assert "Packing Visualization" in resp_viz.text

    def test_workflow_with_group_rules_and_limits(self):
        """User: fragile+heavy items with group rules + weight limit."""
        payload = {
            "items": [
                {"sku": "HOST", "length_cm": 40, "width_cm": 30, "height_cm": 20, "weight_kg": 8},
                {"sku": "ACC", "length_cm": 15, "width_cm": 10, "height_cm": 5, "weight_kg": 0.8},
                {"sku": "FRAG", "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": 0.5, "fragile": True},
                {"sku": "HEAVY", "length_cm": 30, "width_cm": 25, "height_cm": 15, "weight_kg": 25, "heavy": True},
            ],
            "group_rules": [
                {"rule_type": "must_pack_together", "source_sku": "HOST", "target_sku": "ACC", "priority": 1},
                {"rule_type": "must_not_pack_together", "source_sku": "FRAG", "target_sku": "HEAVY", "priority": 1},
            ],
            "shipping_limits": {"max_weight_kg": 30},
            "destination": "6",
            "dual_path": True,
            "verify": False,
        }

        # Pack
        resp = client.post("/pack/direct", json=payload)
        assert resp.status_code == 200
        result = resp.json()
        assert result["strategy"] == "multi_box"
        assert len(result["groups"]) >= 2

        # Each group should have shipping
        for g in result["groups"]:
            if g["box"]:
                assert g["shipping"] is not None

        # Packing list should have multiple boxes
        resp_list = client.post("/pack/list", json=payload)
        list_data = resp_list.json()
        assert len(list_data["boxes"]) >= 2

        # Viz should work with multi-box
        resp_viz = client.post("/pack/viz", json=payload)
        assert resp_viz.status_code == 200

    def test_workflow_dual_path_exceeds_limits(self):
        """User: oversized item → dual-path shows path_b_only."""
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


# ---------------------------------------------------------------------------
# Error handling and validation
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Validate API error responses for invalid input."""

    def test_empty_items_validation_error(self):
        resp = client.post("/pack/direct", json={"items": [], "destination": "6"})
        assert resp.status_code == 422

    def test_missing_required_dimensions(self):
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A"}],
            "destination": "6",
        })
        assert resp.status_code == 422

    def test_negative_dimensions_validation(self):
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A", "length_cm": -10, "width_cm": 20, "height_cm": 15}],
            "destination": "6",
        })
        assert resp.status_code == 422

    def test_zero_dimensions_validation(self):
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A", "length_cm": 0, "width_cm": 20, "height_cm": 15}],
            "destination": "6",
        })
        assert resp.status_code == 422

    def test_invalid_sku_too_long(self):
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A" * 100, "length_cm": 10, "width_cm": 10, "height_cm": 10}],
            "destination": "6",
        })
        assert resp.status_code == 422

    def test_invalid_group_rule_type(self):
        """Unknown rule_type should still be accepted (no strict validation)."""
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A", "length_cm": 10, "width_cm": 10, "height_cm": 10}],
            "group_rules": [{"rule_type": "unknown_type", "source_sku": "A", "target_sku": "B", "priority": 1}],
            "destination": "6",
            "verify": False,
        })
        # API should still process (grouper ignores unknown rule types)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Response content-type and structure validation
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Validate response content-types and structural consistency."""

    def test_pack_direct_returns_json(self):
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A", "length_cm": 10, "width_cm": 10, "height_cm": 10}],
            "destination": "6",
            "verify": False,
        })
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")

    def test_pack_list_returns_json(self):
        resp = client.post("/pack/list", json={
            "items": [{"sku": "A", "length_cm": 10, "width_cm": 10, "height_cm": 10}],
            "destination": "6",
            "verify": False,
        })
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")

    def test_pack_list_text_returns_json_with_text_field(self):
        resp = client.post("/pack/list/text", json={
            "items": [{"sku": "A", "length_cm": 10, "width_cm": 10, "height_cm": 10}],
            "destination": "6",
            "verify": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "text" in data
        assert isinstance(data["text"], str)

    def test_pack_viz_returns_html(self):
        resp = client.post("/pack/viz", json={
            "items": [{"sku": "A", "length_cm": 10, "width_cm": 10, "height_cm": 10}],
            "destination": "6",
            "verify": False,
        })
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "<!DOCTYPE html>" in resp.text

    def test_pack_result_has_required_top_level_keys(self):
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A", "length_cm": 10, "width_cm": 10, "height_cm": 10}],
            "destination": "6",
            "verify": False,
        })
        result = resp.json()
        required_keys = [
            "strategy", "groups", "total_boxes", "feasible_boxes",
            "total_utilization", "group_validation", "summary",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_group_has_required_keys(self):
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A", "length_cm": 10, "width_cm": 10, "height_cm": 10}],
            "destination": "6",
            "verify": False,
        })
        g = resp.json()["groups"][0]
        required_keys = [
            "items", "box", "layout", "utilization", "solve_time_ms",
            "status", "exceeds_limits", "shipping",
        ]
        for key in required_keys:
            assert key in g, f"Missing key in group: {key}"

    def test_shipping_has_required_keys(self):
        resp = client.post("/pack/direct", json={
            "items": [{"sku": "A", "length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 5}],
            "destination": "6",
            "verify": False,
        })
        shipping = resp.json()["groups"][0]["shipping"]
        assert "recommended" in shipping
        assert "all_carriers" in shipping
        assert len(shipping["all_carriers"]) == 3


# ---------------------------------------------------------------------------
# Cross-endpoint data consistency
# ---------------------------------------------------------------------------

class TestCrossEndpointConsistency:
    """Validate data consistency across /pack/direct, /pack/list, /pack/viz."""

    def test_same_input_same_strategy_across_endpoints(self):
        payload = {
            "items": [
                {"sku": "A", "length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 5},
                {"sku": "B", "length_cm": 15, "width_cm": 10, "height_cm": 8, "weight_kg": 2},
            ],
            "destination": "6",
            "verify": False,
        }

        resp_direct = client.post("/pack/direct", json=payload)
        resp_list = client.post("/pack/list", json=payload)

        direct_strategy = resp_direct.json()["strategy"]
        list_strategy = resp_list.json()["metadata"]["strategy"]
        assert direct_strategy == list_strategy

    def test_box_count_matches_in_list(self):
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

        resp_list = client.post("/pack/list", json=payload)
        data = resp_list.json()
        # metadata total_boxes should match actual boxes count
        assert data["metadata"]["total_boxes"] == len(data["boxes"])

    def test_all_skus_appear_in_packing_list(self):
        payload = {
            "items": [
                {"sku": "A", "length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 5},
                {"sku": "B", "length_cm": 15, "width_cm": 10, "height_cm": 8, "weight_kg": 2},
            ],
            "destination": "6",
            "verify": False,
        }

        resp_list = client.post("/pack/list", json=payload)
        data = resp_list.json()

        all_skus = []
        for box in data["boxes"]:
            all_skus.extend(item["sku"] for item in box["items"])
        assert set(all_skus) == {"A", "B"}
