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


def test_high_weight_multiplier_rates_and_shipment_rounding():
    payload = {
        "origin": "HK",
        "destination": "SG",
        "service_type": "priority",
        "packages": [
            {"reference": "BOX-1", "length_cm": 100, "width_cm": 50, "height_cm": 40, "weight_kg": 5},
            {"reference": "BOX-2", "length_cm": 20, "width_cm": 10, "height_cm": 10, "weight_kg": 3},
            {"reference": "BOX-3", "length_cm": 60, "width_cm": 60, "height_cm": 60, "weight_kg": 4},
        ],
    }
    response = client.post("/shipping/quote", json=payload)
    assert response.status_code == 200
    result = response.json()
    carriers = {item["carrier"]: item for item in result["carriers"]}

    assert carriers["DHL"]["shipment_billable_weight_kg"] == 87
    assert carriers["DHL"]["base_rate"] == 8091
    assert carriers["UPS"]["shipment_billable_weight_kg"] == 87
    assert carriers["UPS"]["base_rate"] == 7569
    assert carriers["FedEx"]["shipment_billable_weight_kg"] == 86.5
    assert carriers["FedEx"]["base_rate"] == 8243.45
    assert carriers["FedEx"]["surcharge_total"] == 467.4
    assert [item["code"] for item in carriers["FedEx"]["surcharges"]] == [
        "AHS_DIMENSION", "AHS_DIMENSION"
    ]
    assert carriers["FedEx"]["total"] == 12502.84
    assert result["recommended"] == {"carrier": "DHL", "total": 11003.76}


def test_physical_surcharges_are_per_package_and_exclusive():
    payload = {
        "origin": "HK",
        "destination": "SG",
        "service_type": "priority",
        "packages": [
            {"reference": "BIG", "length_cm": 100, "width_cm": 100, "height_cm": 100, "weight_kg": 1}
        ],
    }
    response = client.post("/shipping/quote", json=payload)
    assert response.status_code == 200
    carriers = {item["carrier"]: item for item in response.json()["carriers"]}

    assert [item["code"] for item in carriers["DHL"]["surcharges"]] == ["OVERWEIGHT_PIECE"]
    assert carriers["DHL"]["surcharge_total"] == 920
    assert [item["code"] for item in carriers["UPS"]["surcharges"]] == ["OVER_MAXIMUM_LIMITS"]
    assert carriers["UPS"]["surcharge_total"] == 2178
    assert [item["code"] for item in carriers["FedEx"]["surcharges"]] == ["UNAUTHORIZED_PACKAGE"]
    assert carriers["FedEx"]["surcharge_total"] == 2193


def test_fedex_dimension_surcharge_enforces_18kg_minimum():
    payload = {
        "origin": "HK",
        "destination": "SG",
        "service_type": "priority",
        "packages": [
            {"reference": "LONG", "length_cm": 122, "width_cm": 10, "height_cm": 10, "weight_kg": 1}
        ],
    }
    result = client.post("/shipping/quote", json=payload).json()
    fedex = next(item for item in result["carriers"] if item["carrier"] == "FedEx")
    assert fedex["packages"][0]["billable_weight_kg"] == 18
    assert fedex["surcharges"][0]["code"] == "AHS_DIMENSION"
