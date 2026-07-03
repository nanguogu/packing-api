"""Acceptance tests for the one-to-five-item cost optimizer."""

from fastapi.testclient import TestClient

from app.main import app
from app.services.level2 import _partitions


client = TestClient(app)


def _order():
    return {
        "order_id": "SG-SIMEI-L2-001",
        "destination": "SG",
        "destination_address": "Singapore Simei MRT Station",
        "service_type": "priority",
        "time_limit_s": 0.75,
        "items": [
            {"sku": "ITEM-1", "length_cm": 50, "width_cm": 50, "height_cm": 5, "weight_kg": 5},
            {"sku": "ITEM-2", "length_cm": 20, "width_cm": 10, "height_cm": 4, "weight_kg": 3},
            {"sku": "ITEM-3", "length_cm": 60, "width_cm": 60, "height_cm": 6, "weight_kg": 4},
            {"sku": "ITEM-4", "length_cm": 100, "width_cm": 50, "height_cm": 4, "weight_kg": 6},
            {"sku": "ITEM-5", "length_cm": 70, "width_cm": 60, "height_cm": 4, "weight_kg": 5},
        ],
    }


def test_level2_partition_generator_has_all_52_partitions():
    partitions = list(_partitions(tuple(range(5))))
    assert len(partitions) == 52
    assert len({tuple(sorted(tuple(sorted(g)) for g in p)) for p in partitions}) == 52


def test_level2_returns_complete_cost_optimized_plan():
    response = client.post("/pack/level2", json=_order())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["level"] == 2
    assert result["packing"]["partition_count"] == 52
    assert result["packing"]["evaluated_plan_count"] >= 52
    assert result["packing"]["carton_count"] >= 1
    assert sum(len(c["item_skus"]) for c in result["packing"]["cartons"]) == 5
    assert sorted(s for c in result["packing"]["cartons"] for s in c["item_skus"]) == [
        "ITEM-1", "ITEM-2", "ITEM-3", "ITEM-4", "ITEM-5"
    ]
    assert result["recommendation"]["shipping_total"] > 0
    assert len(result["alternative_plans"]) <= 2
    assert all(
        plan["shipping_total"] >= result["recommendation"]["shipping_total"]
        for plan in result["alternative_plans"]
    )
    for carton in result["packing"]["cartons"]:
        box = carton["dimensions_cm"]
        for item in carton["layout"]:
            position, size = item["position"], item["placed_dims"]
            assert position["x"] + size["length"] <= box["length_cm"]
            assert position["y"] + size["width"] <= box["width_cm"]
            assert position["z"] + size["height"] <= box["height_cm"]


def test_level2_accepts_four_items():
    payload = _order()
    payload["items"] = payload["items"][:4]
    response = client.post("/pack/level2", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["packing"]["partition_count"] == 15


def test_level2_rejects_zero_or_more_than_five_items():
    payload = _order()
    payload["items"] = []
    assert client.post("/pack/level2", json=payload).status_code == 422
    payload = _order()
    payload["items"].append({
        "sku": "ITEM-6", "length_cm": 10, "width_cm": 10,
        "height_cm": 10, "weight_kg": 1,
    })
    assert client.post("/pack/level2", json=payload).status_code == 422


def test_level2_viz_contains_every_selected_carton():
    response = client.post("/pack/level2/viz", json=_order())
    assert response.status_code == 200
    assert "第 2 层级 3D 装箱指南" in response.text
    assert "SG-SIMEI-L2-001-CARTON-1" in response.text
