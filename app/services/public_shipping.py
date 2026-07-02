"""Versioned public-rate quotation for Hong Kong export shipments.

This module is deliberately separate from the legacy packing estimator.  Public
rate cards are reproducible snapshots; account discounts can later be supplied
by replacing the rate-card provider without changing the quote contract.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


RATE_CARD_PATH = Path(__file__).resolve().parents[1] / "config" / "public_rates_hk_2026.json"


class QuoteUnavailable(ValueError):
    """Raised when the bundled public card cannot price the requested lane."""


def _load_rate_card() -> dict[str, Any]:
    with RATE_CARD_PATH.open(encoding="utf-8") as source:
        return json.load(source)


def _round_up(value: float, increment: float) -> float:
    return math.ceil((value - 1e-12) / increment) * increment


def _package_billable_weight(package: Any, increment: float) -> dict[str, float]:
    dimensional = (
        math.ceil(package.length_cm)
        * math.ceil(package.width_cm)
        * math.ceil(package.height_cm)
        / 5000
    )
    billable = _round_up(max(package.weight_kg, dimensional), increment)
    return {
        "actual_weight_kg": round(package.weight_kg, 3),
        "dimensional_weight_kg": round(dimensional, 3),
        "billable_weight_kg": round(billable, 3),
    }


def _lookup_table_rate(table: dict[str, float], weight: float) -> float:
    key = f"{weight:.1f}"
    if key not in table:
        raise QuoteUnavailable(f"Public snapshot has no rate for {weight:g} kg")
    return float(table[key])


def quote_public_shipment(request: Any) -> dict[str, Any]:
    """Compare all carriers using aggregate multi-piece shipment weight."""
    card = _load_rate_card()
    destination = request.destination.upper()
    carrier_results: list[dict[str, Any]] = []

    for carrier_name, carrier in card["carriers"].items():
        service = carrier["services"].get(request.service_type)
        zone = carrier.get("zones", {}).get(destination)
        if not service or zone is None or zone not in service["rates"]:
            carrier_results.append({
                "carrier": carrier_name,
                "available": False,
                "reason": "service_or_lane_not_in_public_snapshot",
            })
            continue

        increment = float(service["rounding_increment_kg"])
        packages = []
        for package in request.packages:
            weights = _package_billable_weight(package, increment)
            packages.append({"reference": package.reference, **weights})

        # Published multi-piece rules rate one shipment using the sum of each
        # package's independently determined billable weight.
        shipment_weight = _round_up(
            sum(item["billable_weight_kg"] for item in packages), increment
        )
        base = _lookup_table_rate(service["rates"][zone], shipment_weight)
        fuel_rate = float(carrier["fuel_rate"])
        fuel_base = base
        fuel = round(fuel_base * fuel_rate, 2)
        total = round(base + fuel, 2)
        carrier_results.append({
            "carrier": carrier_name,
            "available": True,
            "service_code": service["code"],
            "zone": zone,
            "currency": card["currency"],
            "packages": packages,
            "shipment_billable_weight_kg": round(shipment_weight, 3),
            "base_rate": round(base, 2),
            "surcharges": [],
            "fuel_base": round(fuel_base, 2),
            "fuel_rate": fuel_rate,
            "fuel_surcharge": fuel,
            "taxes": 0.0,
            "total": total,
            "rate_type": carrier["rate_type"],
            "effective_from": carrier["effective_from"],
            "source_url": carrier["source_url"],
        })

    available = [result for result in carrier_results if result["available"]]
    if not available:
        raise QuoteUnavailable("No carrier can price this service/lane/weight from the public snapshot")
    recommended = min(available, key=lambda result: result["total"])
    return {
        "origin": request.origin,
        "destination": destination,
        "service_type": request.service_type,
        "currency": card["currency"],
        "package_count": len(request.packages),
        "pricing_scope": "single_multi_piece_shipment",
        "recommended": {"carrier": recommended["carrier"], "total": recommended["total"]},
        "carriers": carrier_results,
        "notices": card["notices"],
    }
