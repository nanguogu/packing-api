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


def _package_metrics(package: Any, weights: dict[str, float]) -> dict[str, float]:
    longest, second, shortest = sorted(
        (package.length_cm, package.width_cm, package.height_cm), reverse=True
    )
    return {
        "actual_weight_kg": package.weight_kg,
        "billable_weight_kg": weights["billable_weight_kg"],
        "longest_cm": longest,
        "second_longest_cm": second,
        "shortest_cm": shortest,
        "length_girth_cm": longest + 2 * second + 2 * shortest,
        "volume_cm3": package.length_cm * package.width_cm * package.height_cm,
    }


def _condition_matches(condition: dict[str, Any], metrics: dict[str, float]) -> bool:
    if "any" in condition:
        return any(_condition_matches(item, metrics) for item in condition["any"])
    if "all" in condition:
        return all(_condition_matches(item, metrics) for item in condition["all"])
    value = metrics[condition["field"]]
    expected = float(condition["value"])
    return {
        "gt": value > expected,
        "gte": value >= expected,
        "lt": value < expected,
        "lte": value <= expected,
    }[condition["operator"]]


def _package_surcharges(
    package: Any, weights: dict[str, float], rules: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], float]:
    metrics = _package_metrics(package, weights)
    triggered = [rule for rule in rules if _condition_matches(rule["condition"], metrics)]
    winners: dict[str, dict[str, Any]] = {}
    selected: list[dict[str, Any]] = []
    for rule in triggered:
        group = rule.get("exclusive_group")
        if not group:
            selected.append(rule)
            continue
        current = winners.get(group)
        if current is None or int(rule.get("priority", 0)) > int(current.get("priority", 0)):
            winners[group] = rule
    selected.extend(winners.values())
    minimum_billable = max(
        (float(rule.get("minimum_billable_weight_kg", 0)) for rule in triggered),
        default=0,
    )
    return selected, minimum_billable


def _lookup_rate(service: dict[str, Any], zone: str, weight: float) -> float:
    table = service["rates"][zone]
    key = f"{weight:.1f}"
    if key in table:
        return float(table[key])
    for bracket in service.get("per_kg_rates", {}).get(zone, []):
        if float(bracket["min_kg"]) <= weight <= float(bracket["max_kg"]):
            return weight * float(bracket["rate_per_kg"])
    raise QuoteUnavailable(f"Public snapshot has no rate for {weight:g} kg")


def _shipment_increment(service: dict[str, Any], weight: float) -> float:
    for rule in service.get("shipment_rounding", []):
        if weight <= float(rule["max_kg"]):
            return float(rule["increment_kg"])
    return float(service["rounding_increment_kg"])


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
        applied_surcharges: list[dict[str, Any]] = []
        for package in request.packages:
            weights = _package_billable_weight(package, increment)
            package_rules, minimum_billable = _package_surcharges(
                package, weights, carrier.get("surcharge_rules", [])
            )
            if minimum_billable > weights["billable_weight_kg"]:
                weights["billable_weight_kg"] = _round_up(minimum_billable, increment)
            package_surcharges = []
            for rule in package_rules:
                item = {
                    "code": rule["code"],
                    "name": rule["name"],
                    "package_reference": package.reference,
                    "amount": float(rule["amount_hkd"]),
                    "fuel_applicable": bool(rule.get("fuel_applicable", False)),
                }
                package_surcharges.append(item)
                applied_surcharges.append(item)
            packages.append({
                "reference": package.reference,
                **weights,
                "surcharges": package_surcharges,
            })

        # Published multi-piece rules rate one shipment using the sum of each
        # package's independently determined billable weight.
        package_weight_sum = sum(item["billable_weight_kg"] for item in packages)
        shipment_weight = _round_up(
            package_weight_sum, _shipment_increment(service, package_weight_sum)
        )
        base = _lookup_rate(service, zone, shipment_weight)
        fuel_rate = float(carrier["fuel_rate"])
        surcharge_total = sum(item["amount"] for item in applied_surcharges)
        fuel_base = base + sum(
            item["amount"] for item in applied_surcharges if item["fuel_applicable"]
        )
        fuel = round(fuel_base * fuel_rate, 2)
        total = round(base + surcharge_total + fuel, 2)
        carrier_results.append({
            "carrier": carrier_name,
            "available": True,
            "service_code": service["code"],
            "zone": zone,
            "currency": card["currency"],
            "packages": packages,
            "shipment_billable_weight_kg": round(shipment_weight, 3),
            "base_rate": round(base, 2),
            "surcharges": applied_surcharges,
            "surcharge_total": round(surcharge_total, 2),
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
