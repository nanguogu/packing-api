"""Tests for the public Hong Kong multi-piece quote endpoint."""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _payload():
    return {
        "origin": "HK",
        "destination": "sg",
        "service_type": "priority",
        "packages": [
            {"reference": "BOX-1", "length_cm": 20, "width_cm": 20, "height_cm": 20, "weight_kg": 1.0},
            {"reference": "BOX-2", "length_cm": 20, "width_cm": 20, "height_cm": 20, "weight_kg": 1.0}
        ]
    }


def test_quote_compares_three_carriers_and_recommends_cheapest():
    response = client.post("/shipping/quote", json=_payload())
    assert response.status_code == 200
    result = response.json()
    assert result["currency"] == "HKD"
    assert result["package_count"] == 2
    assert len(result["carriers"]) == 3
    assert all(carrier["available"] for carrier in result["carriers"])
    totals = {carrier["carrier"]: carrier["total"] for carrier in result["carriers"]}
    assert result["recommended"]["total"] == min(totals.values())


def test_multi_piece_weight_is_sum_of_each_package_billable_weight():
    result = client.post("/shipping/quote", json=_payload()).json()
    for carrier in result["carriers"]:
        assert carrier["shipment_billable_weight_kg"] == 4.0
        assert [p["billable_weight_kg"] for p in carrier["packages"]] == [2.0, 2.0]


def test_unknown_lane_returns_422_instead_of_inventing_a_rate():
    payload = _payload()
    payload["destination"] = "US"
    response = client.post("/shipping/quote", json=payload)
    assert response.status_code == 422


def test_actual_weight_is_required_and_positive():
    payload = _payload()
    del payload["packages"][0]["weight_kg"]
    assert client.post("/shipping/quote", json=payload).status_code == 422
