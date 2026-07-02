"""Shipping rate comparison and recommendation service.

Core shipping pricing engine for MVP (W2, D6-D10).

Supports three carriers: DHL, UPS, FedEx.
Base rates remain MVP tables; surcharge rules load from JSON or a DB mapping.

Key concepts:
  - Dimensional weight (dim weight): L×W×H ÷ 5000 (metric) or ÷139 (imperial)
  - 2025 Aug: dimensions rounded UP before volume calculation
  - Billable weight = max(actual_weight, dim_weight)
  - Surcharges: DHL (Oversize $30, Overweight $100, fuel chains叠加),
    UPS (AHS $46-58, LPS $219-330, LPS不叠AHS),
    FedEx (AHS $46-58, Oversize $255-330, Oversize不叠AHS)
  - 2026: UPS/FedEx new cubic triggers (10,368in³→AHS, 17,280in³→Oversize)
  - Fuel surcharges: DHL~36%(monthly), UPS/FedEx~46%(weekly)

Calculation flow:
  1. Convert dims to inches (for US carriers) or keep cm (for DHL metric)
  2. Round dims UP (2025 rule)
  3. Calculate dim weight (metric ÷5000, imperial ÷139)
  4. Determine billable weight = max(actual, dim)
  5. Lookup base rate by zone + billable weight
  6. Apply surcharges based on oversize/overweight/AHS triggers
  7. Add fuel surcharge (percentage on total)
  8. Return total cost per carrier
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CM_TO_IN = 1 / 2.54  # cm → inches
IN_TO_CM = 2.54
KG_TO_LB = 2.20462
LB_TO_KG = 1 / 2.20462

# Dimensional weight divisors
DIM_WEIGHT_DIV_METRIC = 5000  # DHL metric: L×W×H(cm) ÷ 5000
DIM_WEIGHT_DIV_IMPERIAL = 139  # UPS/FedEx: L×W×H(in) ÷ 139

# 2025 Aug rule: dimensions rounded UP before volume calculation
ROUND_UP_DIMS = True

# 2026 UPS/FedEx cubic volume triggers (in cubic inches)
UPS_AHS_VOLUME_THRESHOLD = 10368  # → Additional Handling Surcharge
UPS_OVERSIZE_VOLUME_THRESHOLD = 17280  # → Large Package Surcharge (not used yet for FedEx)
FEDEX_AHS_VOLUME_THRESHOLD = 10368
FEDEX_OVERSIZE_VOLUME_THRESHOLD = 17280

# Backwards-compatible defaults. Runtime pricing uses SURCHARGE_CONFIG below.
FUEL_SURCHARGE_DHL = 0.36  # 36% (monthly)
FUEL_SURCHARGE_UPS = 0.46  # 46% (weekly)
FUEL_SURCHARGE_FEDEX = 0.46  # 46% (weekly)


# ---------------------------------------------------------------------------
# Configurable surcharge rules
# ---------------------------------------------------------------------------

@dataclass
class SurchargeRule:
    """A single surcharge rule for a carrier."""
    name: str
    trigger_type: str  # "oversize" | "overweight" | "ahs" | "lps" | "additional_handling"
    trigger_condition: str  # description of when it applies
    amount_usd: float  # flat fee amount
    stacking: str  # "chain" (叠加, cumulative) | "exclusive" (互斥, replaces) | "independent"
    description: str = ""
    condition: Mapping[str, Any] = field(default_factory=dict)
    exclusive_group: str | None = None
    priority: int = 0


SURCHARGE_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "shipping_surcharges.json"


def load_surcharge_config(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Load and validate surcharge configuration from JSON or a DB-style mapping.

    Database adapters can query their tables, assemble the same mapping shape as
    the JSON file, and pass it directly to this function.
    """
    if isinstance(source, Mapping):
        raw = dict(source)
    else:
        with Path(source).open(encoding="utf-8") as config_file:
            raw = json.load(config_file)

    carriers = raw.get("carriers")
    if not isinstance(carriers, Mapping) or not carriers:
        raise ValueError("Surcharge config must contain a non-empty 'carriers' mapping")
    for carrier, settings in carriers.items():
        if not isinstance(settings, Mapping):
            raise ValueError(f"Carrier config for {carrier!r} must be a mapping")
        fuel_rate = settings.get("fuel_rate")
        if not isinstance(fuel_rate, (int, float)) or not 0 <= fuel_rate <= 1:
            raise ValueError(f"Invalid fuel_rate for {carrier!r}")
        if not isinstance(settings.get("rules"), list):
            raise ValueError(f"Carrier config for {carrier!r} must contain a rules list")
        for rule in settings["rules"]:
            required = {"name", "trigger_type", "amount_usd", "stacking", "condition"}
            if not isinstance(rule, Mapping) or not required.issubset(rule):
                raise ValueError(f"Invalid surcharge rule for {carrier!r}: {rule!r}")
    return raw


SURCHARGE_CONFIG = load_surcharge_config(SURCHARGE_CONFIG_PATH)


def set_surcharge_config(source: str | Path | Mapping[str, Any]) -> None:
    """Replace the process-wide surcharge config (useful for DB-backed config)."""
    global SURCHARGE_CONFIG, DHL_SURCHARGES, UPS_SURCHARGES, FEDEX_SURCHARGES
    SURCHARGE_CONFIG = load_surcharge_config(source)
    DHL_SURCHARGES = _rules_for("DHL")
    UPS_SURCHARGES = _rules_for("UPS")
    FEDEX_SURCHARGES = _rules_for("FedEx")


def _rules_for(carrier: str, config: Mapping[str, Any] | None = None) -> list[SurchargeRule]:
    carrier_config = (config or SURCHARGE_CONFIG).get("carriers", {}).get(carrier)
    if not carrier_config:
        return []
    return [
        SurchargeRule(
            name=rule["name"],
            trigger_type=rule["trigger_type"],
            trigger_condition=rule.get("trigger_condition", ""),
            amount_usd=float(rule["amount_usd"]),
            stacking=rule["stacking"],
            description=rule.get("description", ""),
            condition=rule["condition"],
            exclusive_group=rule.get("exclusive_group"),
            priority=int(rule.get("priority", 0)),
        )
        for rule in carrier_config["rules"]
    ]


# Compatibility aliases for callers that inspect the rule lists.
DHL_SURCHARGES = _rules_for("DHL")
UPS_SURCHARGES = _rules_for("UPS")
FEDEX_SURCHARGES = _rules_for("FedEx")


# ---------------------------------------------------------------------------
# Base rate tables (hardcoded for MVP)
# Zones: 1-8 for US domestic, "intl" for international
# Rates per kg (DHL) or per lb (UPS/FedEx), by zone
# ---------------------------------------------------------------------------

# DHL base rates: per kg, by weight bracket
# Simplified MVP table — real rates from PDF parsing (D7)
DHL_BASE_RATES = {
    # zone → {weight_bracket_kg → rate_usd_per_kg}
    "intl": {
        (0, 5): 12.0,
        (5, 10): 10.5,
        (10, 20): 9.0,
        (20, 30): 8.5,
        (30, 50): 7.5,
        (50, 70): 7.0,
        (70, 100): 8.0,  # overweight premium
    },
}

# UPS base rates: per lb, by zone
UPS_BASE_RATES = {
    5: {(0, 1): 15.0, (1, 5): 12.0, (5, 10): 10.0, (10, 30): 8.0, (30, 70): 6.5, (70, 150): 7.5},
    6: {(0, 1): 16.0, (1, 5): 13.0, (5, 10): 11.0, (10, 30): 9.0, (30, 70): 7.0, (70, 150): 8.0},
    7: {(0, 1): 18.0, (1, 5): 14.5, (5, 10): 12.0, (10, 30): 10.0, (30, 70): 8.0, (70, 150): 9.0},
    8: {(0, 1): 20.0, (1, 5): 16.0, (5, 10): 13.5, (10, 30): 11.0, (30, 70): 9.0, (70, 150): 10.0},
}

# FedEx base rates: per lb, by zone
FEDEX_BASE_RATES = {
    5: {(0, 1): 14.0, (1, 5): 11.0, (5, 10): 9.5, (10, 30): 7.5, (30, 70): 6.0, (70, 150): 7.0},
    6: {(0, 1): 15.0, (1, 5): 12.0, (5, 10): 10.5, (10, 30): 8.5, (30, 70): 6.5, (70, 150): 7.5},
    7: {(0, 1): 17.0, (1, 5): 13.5, (5, 10): 11.5, (10, 30): 9.5, (30, 70): 7.5, (70, 150): 8.5},
    8: {(0, 1): 19.0, (1, 5): 15.0, (5, 10): 13.0, (10, 30): 10.5, (30, 70): 8.5, (70, 150): 9.5},
}


# ---------------------------------------------------------------------------
# Core calculation functions
# ---------------------------------------------------------------------------

def calculate_dim_weight_cm(length_cm: float, width_cm: float, height_cm: float) -> float:
    """Calculate dimensional weight in kg using metric formula.

    2025 Aug rule: dimensions rounded UP before multiplication.
    Formula: ceil(L) × ceil(W) × ceil(H) ÷ 5000

    Args:
        length_cm, width_cm, height_cm: Package dimensions in cm.

    Returns:
        Dimensional weight in kg.
    """
    L = math.ceil(length_cm) if ROUND_UP_DIMS else length_cm
    W = math.ceil(width_cm) if ROUND_UP_DIMS else width_cm
    H = math.ceil(height_cm) if ROUND_UP_DIMS else height_cm
    return L * W * H / DIM_WEIGHT_DIV_METRIC


def calculate_dim_weight_in(length_in: float, width_in: float, height_in: float) -> float:
    """Calculate dimensional weight in lb using imperial formula.

    2025 Aug rule: dimensions rounded UP before multiplication.
    Formula: ceil(L) × ceil(W) × ceil(H) ÷ 139

    Args:
        length_in, width_in, height_in: Package dimensions in inches.

    Returns:
        Dimensional weight in lb.
    """
    L = math.ceil(length_in) if ROUND_UP_DIMS else length_in
    W = math.ceil(width_in) if ROUND_UP_DIMS else width_in
    H = math.ceil(height_in) if ROUND_UP_DIMS else height_in
    return L * W * H / DIM_WEIGHT_DIV_IMPERIAL


def calculate_billable_weight(
    actual_weight_kg: float,
    dim_weight_kg: float,
) -> float:
    """Calculate billable weight = max(actual, dim_weight).

    Args:
        actual_weight_kg: Actual weight in kg.
        dim_weight_kg: Dimensional weight in kg.

    Returns:
        Billable weight in kg (the higher of the two).
    """
    return max(actual_weight_kg, dim_weight_kg)


def calculate_girth_in(length_in: float, width_in: float, height_in: float) -> float:
    """Calculate girth = 2 × (width + height) for UPS/FedEx sizing.

    Used in "length + girth" combined dimension metric.
    """
    return 2 * (width_in + height_in)


def calculate_volume_in3(length_in: float, width_in: float, height_in: float) -> float:
    """Calculate volume in cubic inches (for 2026 trigger thresholds)."""
    return length_in * width_in * height_in


def lookup_base_rate(
    carrier: str,
    zone: str | int,
    billable_weight_kg: float,
) -> float | None:
    """Lookup base shipping rate for a carrier, zone, and billable weight.

    Args:
        carrier: "DHL", "UPS", or "FedEx"
        zone: Zone number (1-8 for US) or "intl" for DHL international
        billable_weight_kg: Billable weight in kg

    Returns:
        Base rate in USD, or None if not found.
    """
    if carrier == "DHL":
        rates = DHL_BASE_RATES.get(str(zone) if isinstance(zone, int) else zone)
        if not rates:
            return None
        # DHL rates are per kg
        weight = billable_weight_kg
        for (low, high), rate_per_kg in sorted(rates.items()):
            if low <= weight < high:
                return weight * rate_per_kg
        # Over highest bracket
        max_bracket = max(rates.keys())
        return weight * rates[max_bracket]

    elif carrier == "UPS":
        rates = UPS_BASE_RATES.get(zone)
        if not rates:
            return None
        weight_lb = billable_weight_kg * KG_TO_LB
        for (low, high), rate_per_lb in sorted(rates.items()):
            if low <= weight_lb < high:
                return weight_lb * rate_per_lb
        max_bracket = max(rates.keys())
        return weight_lb * rates[max_bracket]

    elif carrier == "FedEx":
        rates = FEDEX_BASE_RATES.get(zone)
        if not rates:
            return None
        weight_lb = billable_weight_kg * KG_TO_LB
        for (low, high), rate_per_lb in sorted(rates.items()):
            if low <= weight_lb < high:
                return weight_lb * rate_per_lb
        max_bracket = max(rates.keys())
        return weight_lb * rates[max_bracket]

    return None


# ---------------------------------------------------------------------------
# Surcharge calculation
# ---------------------------------------------------------------------------

_COMPARISON_OPERATORS = {
    "gt": lambda actual, expected: actual > expected,
    "gte": lambda actual, expected: actual >= expected,
    "lt": lambda actual, expected: actual < expected,
    "lte": lambda actual, expected: actual <= expected,
    "eq": lambda actual, expected: actual == expected,
}


def _condition_matches(condition: Mapping[str, Any], metrics: Mapping[str, float]) -> bool:
    """Evaluate a declarative condition tree against package metrics."""
    if "always" in condition:
        return bool(condition["always"])
    if "any" in condition:
        return any(_condition_matches(item, metrics) for item in condition["any"])
    if "all" in condition:
        return all(_condition_matches(item, metrics) for item in condition["all"])

    field_name = condition.get("field")
    operator_name = condition.get("operator")
    if field_name not in metrics:
        raise ValueError(f"Unknown surcharge metric: {field_name!r}")
    if operator_name not in _COMPARISON_OPERATORS:
        raise ValueError(f"Unknown surcharge operator: {operator_name!r}")
    return _COMPARISON_OPERATORS[operator_name](metrics[field_name], condition.get("value"))

def calculate_surcharges(
    carrier: str,
    length_cm: float,
    width_cm: float,
    height_cm: float,
    actual_weight_kg: float,
    config: Mapping[str, Any] | None = None,
) -> list[dict]:
    """Determine which surcharges apply and calculate their amounts.

    Args:
        carrier: "DHL", "UPS", or "FedEx"
        length_cm, width_cm, height_cm: Package dimensions in cm
        actual_weight_kg: Actual weight in kg
        config: Optional loaded configuration; defaults to the process config.

    Returns:
        List of applicable surcharge dicts with keys:
          - name, trigger_type, amount_usd, stacking
    """
    # Compute a carrier-neutral set of metrics once; rules only reference names.
    length_in = length_cm * CM_TO_IN
    width_in = width_cm * CM_TO_IN
    height_in = height_cm * CM_TO_IN
    actual_weight_lb = actual_weight_kg * KG_TO_LB
    volume_in3 = calculate_volume_in3(
        math.ceil(length_in), math.ceil(width_in), math.ceil(height_in)
    )
    girth_in = calculate_girth_in(length_in, width_in, height_in)
    length_plus_girth = length_in + girth_in

    metrics = {
        "length_cm": length_cm,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "max_dimension_cm": max(length_cm, width_cm, height_cm),
        "actual_weight_kg": actual_weight_kg,
        "length_in": length_in,
        "width_in": width_in,
        "height_in": height_in,
        "max_dimension_in": max(length_in, width_in, height_in),
        "actual_weight_lb": actual_weight_lb,
        "volume_in3": volume_in3,
        "girth_in": girth_in,
        "length_plus_girth_in": length_plus_girth,
    }

    triggered = [rule for rule in _rules_for(carrier, config) if _condition_matches(rule.condition, metrics)]

    # In each exclusive group, only the highest-priority triggered rule survives.
    winners: dict[str, SurchargeRule] = {}
    for rule in triggered:
        if rule.exclusive_group:
            current = winners.get(rule.exclusive_group)
            if current is None or rule.priority > current.priority:
                winners[rule.exclusive_group] = rule

    selected = [
        rule for rule in triggered
        if not rule.exclusive_group or winners[rule.exclusive_group] is rule
    ]
    return [
        {
            "name": rule.name,
            "trigger_type": rule.trigger_type,
            "amount_usd": rule.amount_usd,
            "stacking": rule.stacking,
        }
        for rule in selected
    ]


def calculate_total_cost(
    base_rate: float,
    surcharges: list[dict],
    fuel_rate: float,
) -> float:
    """Calculate total shipping cost with surcharge stacking logic.

    Stacking rules:
      - "chain" (DHL): all surcharges are cumulative → sum of flat fees
        then fuel % on total
      - "exclusive" (UPS LPS / FedEx Oversize): replaces AHS, not added on top
      - "independent" (UPS AHS / FedEx AHS): added as flat fee

    Fuel surcharge is always applied as % on (base + all flat surcharges).

    Args:
        base_rate: Base shipping cost in USD
        surcharges: List of surcharge dicts from calculate_surcharges
        fuel_rate: Fuel surcharge rate as decimal (0.36 = 36%)

    Returns:
        Total shipping cost in USD.
    """
    # Sum flat surcharge fees (fuel has amount=0, it's percentage-based)
    flat_surcharges = sum(s["amount_usd"] for s in surcharges if s["trigger_type"] != "fuel")

    # Total before fuel
    subtotal = base_rate + flat_surcharges

    # Apply fuel surcharge as percentage
    fuel_amount = subtotal * fuel_rate

    # Total = base + flat surcharges + fuel
    total = subtotal + fuel_amount

    return round(total, 2)


# ---------------------------------------------------------------------------
# Full comparison function
# ---------------------------------------------------------------------------

def get_shipping_recommendation(
    box_dimensions: dict,
    total_weight_kg: float,
    destination: str | int = "intl",
    surcharge_config: Mapping[str, Any] | None = None,
) -> dict:
    """Get shipping rate comparison and recommendation for a packed box.

    Calculates costs for all three carriers (DHL, UPS, FedEx) and
    recommends the cheapest option.

    Args:
        box_dimensions: Dict with keys: length_cm, width_cm, height_cm.
        total_weight_kg: Total weight of items in the box.
        destination: Shipping zone ("intl" for DHL, zone 5-8 for UPS/FedEx).
        surcharge_config: Optional loaded JSON/DB mapping for this calculation.

    Returns:
        Dict with keys:
          - recommended: best option dict (carrier, cost_usd, estimated_days, details)
          - alternatives: list of other viable options
          - details: per-carrier breakdown with dim_weight, billable_weight, surcharges
          - total_weight_kg: confirmed actual weight
    """
    L_cm = box_dimensions["length_cm"]
    W_cm = box_dimensions["width_cm"]
    H_cm = box_dimensions["height_cm"]

    # Calculate dimensional weights
    dim_weight_kg_metric = calculate_dim_weight_cm(L_cm, W_cm, H_cm)
    dim_weight_lb_imperial = calculate_dim_weight_in(
        L_cm * CM_TO_IN, W_cm * CM_TO_IN, H_cm * CM_TO_IN
    )
    dim_weight_kg_imperial = dim_weight_lb_imperial * LB_TO_KG

    # Billable weight for each carrier
    billable_kg_dhl = calculate_billable_weight(total_weight_kg, dim_weight_kg_metric)
    billable_kg_ups = calculate_billable_weight(total_weight_kg, dim_weight_kg_imperial)
    billable_kg_fedex = calculate_billable_weight(total_weight_kg, dim_weight_kg_imperial)

    active_surcharge_config = surcharge_config or SURCHARGE_CONFIG

    # Calculate for each carrier
    carriers = ["DHL", "UPS", "FedEx"]
    results = []

    for carrier in carriers:
        if carrier == "DHL":
            bw = billable_kg_dhl
            zone = "intl"
        else:
            bw = billable_kg_ups if carrier == "UPS" else billable_kg_fedex
            zone = int(destination) if isinstance(destination, str) and destination.isdigit() else destination
            if isinstance(zone, str):
                zone = 6  # default zone for MVP
        fuel = float(active_surcharge_config["carriers"][carrier]["fuel_rate"])

        base_rate = lookup_base_rate(carrier, zone, bw)
        if base_rate is None:
            continue

        surcharges = calculate_surcharges(
            carrier, L_cm, W_cm, H_cm, total_weight_kg, active_surcharge_config
        )
        total_cost = calculate_total_cost(base_rate, surcharges, fuel)

        results.append({
            "carrier": carrier,
            "cost_usd": total_cost,
            "base_rate_usd": round(base_rate, 2),
            "billable_weight_kg": round(bw, 2),
            "dim_weight_kg": round(dim_weight_kg_metric if carrier == "DHL" else dim_weight_kg_imperial, 2),
            "actual_weight_kg": total_weight_kg,
            "surcharges": surcharges,
            "fuel_rate": fuel,
            "zone": zone,
            "estimated_days": _estimate_delivery_days(carrier, zone),
        })

    # Sort by cost → recommend cheapest
    results.sort(key=lambda r: r["cost_usd"])

    recommended = results[0] if results else None
    alternatives = results[1:] if len(results) > 1 else []

    return {
        "recommended": recommended,
        "alternatives": alternatives,
        "all_carriers": results,
        "total_weight_kg": total_weight_kg,
        "box_dimensions": box_dimensions,
        "dim_weight_metric_kg": round(dim_weight_kg_metric, 2),
        "dim_weight_imperial_kg": round(dim_weight_kg_imperial, 2),
    }


def _estimate_delivery_days(carrier: str, zone: str | int) -> int:
    """Estimate delivery days for MVP (rough approximation)."""
    if carrier == "DHL":
        return 3  # DHL international: ~3 days
    elif carrier == "UPS":
        zone = int(zone) if isinstance(zone, int) else 6
        return 1 + zone // 2  # ~1-5 days for domestic
    elif carrier == "FedEx":
        zone = int(zone) if isinstance(zone, int) else 6
        return 1 + zone // 2
    return 5
