"""End-to-end acceptance tests for the Level 1 packing and quote flow."""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _singapore_order() -> dict:
    return {
        "order_id": "SG-GOV-001",
        "origin": "HK",
        "destination": "SG",
        "destination_address": "Singapore Government Building",
        "service_type": "priority",
        "items": [
            {"sku": "ITEM-1", "length_cm": 100, "width_cm": 50, "height_cm": 40, "weight_kg": 5},
            {"sku": "ITEM-2", "length_cm": 20, "width_cm": 10, "height_cm": 10, "weight_kg": 3},
            {"sku": "ITEM-3", "length_cm": 60, "width_cm": 60, "height_cm": 60, "weight_kg": 4},
        ],
    }


def test_level1_singapore_order_returns_optimal_single_carton_and_quote():
    response = client.post("/pack/level1", json=_singapore_order())
    assert response.status_code == 200
    result = response.json()

    assert result["level"] == 1
    assert result["strategy"] == "single_custom_carton"
    assert result["packing"]["carton_count"] == 1
    carton = result["packing"]["carton"]
    assert sorted(carton["dimensions_cm"].values()) == [60, 60, 160]
    assert carton["volume_cm3"] == 576_000
    assert carton["actual_weight_kg"] == 12
    assert result["packing"]["solver_status"] == "OPTIMAL"
    assert result["packing"]["objective"] == "minimum_carton_volume"

    assert result["recommendation"] == {
        "carton_strategy": "single_custom_carton",
        "carrier": "UPS",
        "currency": "HKD",
        "shipping_total": 15503.74,
    }
    assert len(result["shipping"]["carriers"]) == 3


def test_level1_layout_stays_inside_returned_carton():
    result = client.post("/pack/level1", json=_singapore_order()).json()
    box = result["packing"]["carton"]["dimensions_cm"]
    for item in result["packing"]["layout"]:
        position = item["position"]
        size = item["placed_dims"]
        assert position["x"] + size["length"] <= box["length_cm"]
        assert position["y"] + size["width"] <= box["width_cm"]
        assert position["z"] + size["height"] <= box["height_cm"]


def test_level1_requires_exactly_three_items():
    payload = _singapore_order()
    payload["items"] = payload["items"][:2]
    assert client.post("/pack/level1", json=payload).status_code == 422


def test_level1_rejects_unavailable_public_rate_lane():
    payload = _singapore_order()
    payload["destination"] = "JP"
    response = client.post("/pack/level1", json=payload)
    assert response.status_code == 422
    assert "No carrier can price" in response.json()["detail"]
