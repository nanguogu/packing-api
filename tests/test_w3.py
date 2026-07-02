"""Tests for D11-D13: viz endpoint, packing list endpoints.

Validates:
  - /pack/viz returns valid HTML with Plotly.js
  - /pack/list returns structured packing list dict
  - /pack/list/text returns human-readable text packing list
  - viz.py generate_3d_html() produces correct HTML structure
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.viz import generate_3d_html, _cuboid_mesh, _generate_colors
from app.services.packer import pack_items
from app.services.packing_list import generate_packing_list, generate_packing_list_text


client = TestClient(app)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _item(sku, L, W, H, weight=0.5, fragile=False, heavy=False):
    return {
        "sku": sku, "length_cm": L, "width_cm": W,
        "height_cm": H, "weight_kg": weight,
        "fragile": fragile, "heavy": heavy,
    }

BASE_PAYLOAD = {
    "items": [
        {"sku": "HOST", "length_cm": 40, "width_cm": 30, "height_cm": 20, "weight_kg": 8},
        {"sku": "ACC", "length_cm": 15, "width_cm": 10, "height_cm": 5, "weight_kg": 0.8},
        {"sku": "CABLE", "length_cm": 5, "width_cm": 5, "height_cm": 3, "weight_kg": 0.2},
    ],
    "destination": "6",
    "verify": False,
}


# ---------------------------------------------------------------------------
# viz.py unit tests
# ---------------------------------------------------------------------------

class TestCuboidMesh:
    """Test _cuboid_mesh vertex and triangle generation."""

    def test_basic_cuboid(self):
        mesh = _cuboid_mesh(0, 0, 0, 10, 20, 30)
        assert len(mesh["vx"]) == 8
        assert len(mesh["vy"]) == 8
        assert len(mesh["vz"]) == 8
        assert len(mesh["i"]) == 12
        assert len(mesh["j"]) == 12
        assert len(mesh["k"]) == 12

    def test_cuboid_with_offset(self):
        mesh = _cuboid_mesh(5, 10, 15, 10, 20, 30)
        assert mesh["vx"][0] == 5
        assert mesh["vx"][1] == 15  # 5 + 10
        assert mesh["vy"][0] == 10
        assert mesh["vz"][0] == 15

    def test_cuboid_vertices_span_correct_range(self):
        mesh = _cuboid_mesh(0, 0, 0, 30, 20, 15)
        assert max(mesh["vx"]) == 30
        assert max(mesh["vy"]) == 20
        assert max(mesh["vz"]) == 15
        assert min(mesh["vx"]) == 0
        assert min(mesh["vy"]) == 0
        assert min(mesh["vz"]) == 0

    def test_cuboid_has_twelve_unique_non_degenerate_triangles(self):
        mesh = _cuboid_mesh(0, 0, 0, 10, 20, 30)
        vertices = list(zip(mesh["vx"], mesh["vy"], mesh["vz"]))
        triangles = list(zip(mesh["i"], mesh["j"], mesh["k"]))

        assert len({tuple(sorted(face)) for face in triangles}) == 12
        for a, b, c in triangles:
            va, vb, vc = vertices[a], vertices[b], vertices[c]
            ab = tuple(vb[n] - va[n] for n in range(3))
            ac = tuple(vc[n] - va[n] for n in range(3))
            cross = (
                ab[1] * ac[2] - ab[2] * ac[1],
                ab[2] * ac[0] - ab[0] * ac[2],
                ab[0] * ac[1] - ab[1] * ac[0],
            )
            assert cross != (0, 0, 0)


class TestGenerateColors:
    """Test _generate_colors palette generation."""

    def test_small_n_returns_palette_subset(self):
        colors = _generate_colors(5)
        assert len(colors) == 5
        assert all(c.startswith("#") for c in colors)

    def test_large_n_generates_hsv_colors(self):
        colors = _generate_colors(20)
        assert len(colors) == 20
        assert all(c.startswith("#") for c in colors)

    def test_custom_base_colors(self):
        custom = ["#ff0000", "#00ff00", "#0000ff"]
        colors = _generate_colors(3, base_colors=custom)
        assert colors == custom

    def test_custom_colors_insufficient_falls_back(self):
        custom = ["#ff0000"]
        colors = _generate_colors(5, base_colors=custom)
        assert len(colors) == 5
        # Custom color should be used first, then palette fills rest
        assert colors[0] == "#ff0000"


class TestGenerate3DHtml:
    """Test generate_3d_html function."""

    def test_basic_html_generation(self):
        items = [_item("A", 30, 20, 15, 5)]
        result = pack_items(items, verify=False)
        html = generate_3d_html(result)

        assert "<!DOCTYPE html>" in html
        assert "plotly" in html.lower()
        assert "Packing Visualization" in html
        assert "stepForward" in html
        assert "stepBack" in html

    def test_empty_result_html(self):
        html = generate_3d_html({"groups": []})
        assert "No items to visualize" in html

    def test_multi_group_html(self):
        items = [
            _item("FRAG", 10, 10, 10, 0.5, fragile=True),
            _item("HEAVY", 30, 20, 15, 50, heavy=True),
        ]
        rules = [
            {"rule_type": "must_not_pack_together", "source_sku": "FRAG",
             "target_sku": "HEAVY", "priority": 1},
        ]
        result = pack_items(items, group_rules=rules, verify=False)
        html = generate_3d_html(result)

        assert "<!DOCTYPE html>" in html
        # Should have wireframe traces for both boxes
        assert "scatter3d" in html

    def test_html_contains_item_data(self):
        items = [_item("BOX-1", 30, 20, 15, 5)]
        result = pack_items(items, verify=False)
        html = generate_3d_html(result)

        assert "BOX-1" in html
        assert "mesh3d" in html

    def test_html_has_keyboard_shortcuts(self):
        items = [_item("A", 30, 20, 15, 5)]
        result = pack_items(items, verify=False)
        html = generate_3d_html(result)

        assert "ArrowRight" in html
        assert "ArrowLeft" in html

    def test_animation_indices_target_item_meshes(self):
        items = [_item("A", 30, 20, 15, 5)]
        html = generate_3d_html(pack_items(items, verify=False))
        match = re.search(r"window\.BOXVIZ_DATA = (.*?);", html)
        data = json.loads(match.group(1))
        assert data["stepGroups"] == [[12]]  # 12 wireframe traces precede first mesh

    def test_multiple_boxes_are_offset_side_by_side(self):
        items = [_item("A", 10, 10, 10), _item("B", 12, 8, 6)]
        rules = [{
            "rule_type": "must_not_pack_together", "source_sku": "A",
            "target_sku": "B", "priority": 1,
        }]
        html = generate_3d_html(pack_items(items, group_rules=rules, verify=False))
        match = re.search(r"var traces = (.*?);\n", html)
        traces = json.loads(match.group(1))
        meshes = [trace for trace in traces if trace["type"] == "mesh3d"]
        assert max(meshes[0]["x"]) < min(meshes[1]["x"])
        assert meshes[0]["color"] != meshes[1]["color"]


# ---------------------------------------------------------------------------
# /pack/viz API endpoint tests
# ---------------------------------------------------------------------------

class TestPackVizEndpoint:
    """Test /pack/viz API endpoint."""

    def test_viz_returns_html(self):
        resp = client.post("/pack/viz", json=BASE_PAYLOAD)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        html_body = resp.text
        assert "<!DOCTYPE html>" in html_body
        assert "Packing Visualization" in html_body

    def test_viz_with_group_rules(self):
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
        resp = client.post("/pack/viz", json=payload)
        assert resp.status_code == 200
        html_body = resp.text
        assert "<!DOCTYPE html>" in html_body

    def test_viz_with_dual_path(self):
        payload = {
            **BASE_PAYLOAD,
            "shipping_limits": {"max_length_cm": 50, "max_width_cm": 40, "max_height_cm": 30},
            "dual_path": True,
        }
        resp = client.post("/pack/viz", json=payload)
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text

    def test_viz_single_item(self):
        payload = {
            "items": [
                {"sku": "SINGLE", "length_cm": 20, "width_cm": 15, "height_cm": 10, "weight_kg": 2},
            ],
            "destination": "6",
        }
        resp = client.post("/pack/viz", json=payload)
        assert resp.status_code == 200
        assert "SINGLE" in resp.text


# ---------------------------------------------------------------------------
# /pack/list API endpoint tests
# ---------------------------------------------------------------------------

class TestPackListEndpoint:
    """Test /pack/list API endpoint."""

    def test_list_returns_structured_dict(self):
        resp = client.post("/pack/list", json=BASE_PAYLOAD)
        assert resp.status_code == 200

        data = resp.json()
        assert "metadata" in data
        assert "boxes" in data
        assert "shipping_summary" in data
        assert "generated_at" in data

        meta = data["metadata"]
        assert meta["strategy"] == "single_box"
        assert meta["total_boxes"] >= 1

    def test_list_box_details(self):
        resp = client.post("/pack/list", json=BASE_PAYLOAD)
        data = resp.json()

        box = data["boxes"][0]
        assert "box_id" in box
        assert "dimensions" in box
        assert "items" in box
        assert "total_weight_kg" in box
        assert "utilization" in box
        assert "item_count" in box
        assert len(box["items"]) == 3

    def test_list_with_group_rules(self):
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
        resp = client.post("/pack/list", json=payload)
        data = resp.json()

        assert data["metadata"]["strategy"] == "multi_box"
        assert len(data["boxes"]) == 2

    def test_list_with_shipping_limits(self):
        payload = {
            **BASE_PAYLOAD,
            "shipping_limits": {"max_weight_kg": 30},
        }
        resp = client.post("/pack/list", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["metadata"]["total_boxes"] >= 1


# ---------------------------------------------------------------------------
# /pack/list/text API endpoint tests
# ---------------------------------------------------------------------------

class TestPackListItemTextEndpoint:
    """Test /pack/list/text API endpoint."""

    def test_text_returns_text_string(self):
        resp = client.post("/pack/list/text", json=BASE_PAYLOAD)
        assert resp.status_code == 200

        data = resp.json()
        assert "text" in data
        text = data["text"]

        assert "PACKING LIST" in text
        assert "Strategy:" in text
        assert "Total boxes:" in text
        assert "Box 1:" in text

    def test_text_contains_item_details(self):
        resp = client.post("/pack/list/text", json=BASE_PAYLOAD)
        text = resp.json()["text"]

        assert "HOST" in text
        assert "ACC" in text
        assert "CABLE" in text

    def test_text_with_multi_box(self):
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
        resp = client.post("/pack/list/text", json=payload)
        text = resp.json()["text"]

        assert "multi_box" in text
        assert "Box 1:" in text
        assert "Box 2:" in text
