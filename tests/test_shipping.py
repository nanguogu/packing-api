"""Tests for shipping rate comparison service (W2, D6-D8).

Validates: dim weight calculation, surcharge triggers, cost stacking,
full comparison, and recommendation logic.

Key acceptance criteria:
  - Dim weight: L*W*H / 5000 (metric), L*W*H / 139 (imperial)
  - 2025 Aug: dims rounded UP before volume calc
  - DHL: Oversize $30 + Overweight $100 (chain叠加) + fuel 36%
  - UPS: AHS $46 OR LPS $219 (LPS replaces AHS) + fuel 46%
  - FedEx: AHS $46 OR Oversize $255 (Oversize replaces AHS) + fuel 46%
  - Billable weight = max(actual, dim_weight)
  - Recommendation = cheapest carrier
"""

import pytest
import math
from copy import deepcopy
from app.services.shipping import (
    calculate_dim_weight_cm,
    calculate_dim_weight_in,
    calculate_billable_weight,
    calculate_girth_in,
    calculate_volume_in3,
    calculate_surcharges,
    calculate_total_cost,
    get_shipping_recommendation,
    lookup_base_rate,
    CM_TO_IN,
    KG_TO_LB,
    FUEL_SURCHARGE_DHL,
    FUEL_SURCHARGE_UPS,
    FUEL_SURCHARGE_FEDEX,
    SURCHARGE_CONFIG,
    SURCHARGE_CONFIG_PATH,
    load_surcharge_config,
)


# ---------------------------------------------------------------------------
# Dimensional weight tests
# ---------------------------------------------------------------------------

class TestDimWeightMetric:
    """Dim weight calculation using metric formula (L*W*H / 5000)."""

    def test_simple_cube(self):
        """10*10*10 cm cube → dim_weight = 1000/5000 = 0.2 kg."""
        result = calculate_dim_weight_cm(10, 10, 10)
        assert result == 0.2

    def test_rounding_up_2025_rule(self):
        """Dims should be rounded UP before volume calc (2025 Aug rule).
        9.5*9.5*9.5 → ceil → 10*10*10 → 0.2 kg
        """
        result = calculate_dim_weight_cm(9.5, 9.5, 9.5)
        # ceil(9.5)=10, 10*10*10/5000 = 0.2
        assert result == 0.2

    def test_large_package(self):
        """60*40*30 cm → dim_weight = 72000/5000 = 14.4 kg."""
        result = calculate_dim_weight_cm(60, 40, 30)
        assert result == 14.4

    def test_exactly_integer_dims(self):
        """Integer dims should not be affected by rounding."""
        result = calculate_dim_weight_cm(50, 40, 30)
        # 50*40*30 = 60000 / 5000 = 12.0
        assert result == 12.0


class TestDimWeightImperial:
    """Dim weight calculation using imperial formula (L*W*H / 139)."""

    def test_simple_box_inches(self):
        """10*8*6 in → dim_weight = 480/139 ≈ 3.45 lb."""
        result = calculate_dim_weight_in(10, 8, 6)
        assert round(result, 2) == 3.45

    def test_rounding_up_in_inches(self):
        """Dims rounded UP in inches too."""
        result = calculate_dim_weight_in(9.5, 7.5, 5.5)
        # ceil → 10*8*6 = 480 / 139 ≈ 3.45
        assert round(result, 2) == 3.45

    def test_large_package_inches(self):
        """48*36*24 in → 41472/139 ≈ 298.36 lb."""
        result = calculate_dim_weight_in(48, 36, 24)
        assert round(result, 2) == 298.36


# ---------------------------------------------------------------------------
# Billable weight tests
# ---------------------------------------------------------------------------

class TestBillableWeight:
    """Billable weight = max(actual, dim_weight)."""

    def test_actual_heavier(self):
        """When actual weight > dim weight, billable = actual."""
        bw = calculate_billable_weight(10.0, 5.0)
        assert bw == 10.0

    def test_dim_weight_heavier(self):
        """When dim weight > actual, billable = dim weight."""
        bw = calculate_billable_weight(2.0, 14.4)
        assert bw == 14.4

    def test_equal_weights(self):
        """When actual == dim, billable = that weight."""
        bw = calculate_billable_weight(5.0, 5.0)
        assert bw == 5.0


# ---------------------------------------------------------------------------
# Surcharge trigger tests
# ---------------------------------------------------------------------------

class TestDHLSurcharges:
    """DHL surcharge triggers and chain stacking."""

    def test_no_surcharges_small_package(self):
        """Small package under DHL limits → only fuel surcharge."""
        surcharges = calculate_surcharges("DHL", 30, 20, 15, 5)
        # Should only have fuel (oversize dim<120, weight<70)
        non_fuel = [s for s in surcharges if s["trigger_type"] != "fuel"]
        assert len(non_fuel) == 0

    def test_oversize_trigger(self):
        """Dim > 120cm triggers DHL Oversize ($30)."""
        surcharges = calculate_surcharges("DHL", 130, 40, 30, 10)
        oversize = [s for s in surcharges if s["trigger_type"] == "oversize"]
        assert len(oversize) == 1
        assert oversize[0]["amount_usd"] == 30

    def test_overweight_trigger(self):
        """Weight > 70kg triggers DHL Overweight ($100)."""
        surcharges = calculate_surcharges("DHL", 40, 30, 20, 75)
        overweight = [s for s in surcharges if s["trigger_type"] == "overweight"]
        assert len(overweight) == 1
        assert overweight[0]["amount_usd"] == 100

    def test_both_oversize_and_overweight(self):
        """DHL chains: both oversize + overweight = $30 + $100 = $130 flat."""
        surcharges = calculate_surcharges("DHL", 130, 40, 30, 75)
        flat_surcharges = [s for s in surcharges if s["trigger_type"] != "fuel"]
        total_flat = sum(s["amount_usd"] for s in flat_surcharges)
        assert total_flat == 130  # $30 + $100


class TestUPSSurcharges:
    """UPS surcharge triggers: AHS vs LPS (LPS replaces AHS)."""

    def test_no_surcharges_small_package(self):
        """Small package → only fuel surcharge."""
        surcharges = calculate_surcharges("UPS", 30, 20, 15, 5)
        non_fuel = [s for s in surcharges if s["trigger_type"] != "fuel"]
        assert len(non_fuel) == 0

    def test_ahs_by_dimension(self):
        """Dim > 48in triggers UPS AHS ($46)."""
        # 50cm ≈ 19.7in, 125cm ≈ 49.2in > 48in
        surcharges = calculate_surcharges("UPS", 125, 40, 30, 10)
        ahs = [s for s in surcharges if s["trigger_type"] == "ahs"]
        assert len(ahs) == 1
        assert ahs[0]["amount_usd"] == 46

    def test_ahs_by_weight(self):
        """Weight > 70lb triggers UPS AHS ($46)."""
        # 35kg ≈ 77lb > 70lb
        surcharges = calculate_surcharges("UPS", 30, 20, 15, 35)
        ahs = [s for s in surcharges if s["trigger_type"] == "ahs"]
        assert len(ahs) == 1

    def test_lps_replaces_ahs(self):
        """LPS (>130in length+girth) REPLACES AHS, not stacked."""
        # Large box: 100cm*80cm*60cm ≈ 39*31*24in
        # girth = 2*(31+24) = 110in, length+girth = 39+110 = 149in > 130
        surcharges = calculate_surcharges("UPS", 100, 80, 60, 15)
        # Should have LPS but NOT AHS
        has_ahs = any(s["trigger_type"] == "ahs" for s in surcharges)
        has_lps = any(s["trigger_type"] == "lps" for s in surcharges)
        assert not has_ahs  # LPS replaces AHS
        assert has_lps


class TestFedExSurcharges:
    """FedEx surcharge triggers: AHS vs Oversize (Oversize replaces AHS)."""

    def test_no_surcharges_small_package(self):
        """Small package → only fuel surcharge."""
        surcharges = calculate_surcharges("FedEx", 30, 20, 15, 5)
        non_fuel = [s for s in surcharges if s["trigger_type"] != "fuel"]
        assert len(non_fuel) == 0

    def test_ahs_by_dimension(self):
        """Dim > 48in triggers FedEx AHS ($46)."""
        surcharges = calculate_surcharges("FedEx", 125, 40, 30, 10)
        ahs = [s for s in surcharges if s["trigger_type"] == "ahs"]
        assert len(ahs) == 1
        assert ahs[0]["amount_usd"] == 46

    def test_oversize_replaces_ahs(self):
        """FedEx Oversize replaces AHS (not stacked)."""
        # Volume > 17280in³ triggers Oversize
        # 100cm*80cm*60cm → 39*31*24in → volume ≈ 29,184in³ > 17,280
        surcharges = calculate_surcharges("FedEx", 100, 80, 60, 15)
        has_ahs = any(s["trigger_type"] == "ahs" for s in surcharges)
        has_oversize = any(s["trigger_type"] == "oversize" for s in surcharges)
        assert not has_ahs  # Oversize replaces AHS
        assert has_oversize


class TestConfigurableSurcharges:
    """Rules can come from JSON files or database-shaped mappings."""

    def test_loads_default_json_config(self):
        config = load_surcharge_config(SURCHARGE_CONFIG_PATH)
        assert set(config["carriers"]) == {"DHL", "UPS", "FedEx"}

    def test_accepts_database_style_mapping(self):
        config = deepcopy(SURCHARGE_CONFIG)
        config["carriers"]["DHL"]["rules"][0]["amount_usd"] = 77
        loaded = load_surcharge_config(config)

        surcharges = calculate_surcharges("DHL", 130, 40, 30, 10, loaded)
        oversize = next(s for s in surcharges if s["trigger_type"] == "oversize")
        assert oversize["amount_usd"] == 77

    def test_config_controls_fuel_rate(self):
        config = deepcopy(SURCHARGE_CONFIG)
        config["carriers"]["UPS"]["fuel_rate"] = 0
        box = {"length_cm": 30, "width_cm": 20, "height_cm": 15}

        result = get_shipping_recommendation(box, 5, 6, config)
        ups = next(c for c in result["all_carriers"] if c["carrier"] == "UPS")
        assert ups["cost_usd"] == ups["base_rate_usd"]


# ---------------------------------------------------------------------------
# Total cost calculation tests
# ---------------------------------------------------------------------------

class TestTotalCost:
    """Total cost = base + surcharges + fuel%."""

    def test_dhl_chain_stacking_with_fuel(self):
        """DHL: base + oversize + overweight + fuel 36% on subtotal."""
        base = 100
        surcharges = [
            {"name": "DHL Oversize", "trigger_type": "oversize", "amount_usd": 30, "stacking": "chain"},
            {"name": "DHL Overweight", "trigger_type": "overweight", "amount_usd": 100, "stacking": "chain"},
            {"name": "DHL Fuel", "trigger_type": "fuel", "amount_usd": 0, "stacking": "chain"},
        ]
        total = calculate_total_cost(base, surcharges, FUEL_SURCHARGE_DHL)
        # subtotal = 100 + 30 + 100 = 230
        # fuel = 230 * 0.36 = 82.8
        # total = 230 + 82.8 = 312.8
        assert total == 312.8

    def test_ups_lps_only_with_fuel(self):
        """UPS: base + LPS + fuel 46% (no AHS because LPS replaces it)."""
        base = 80
        surcharges = [
            {"name": "UPS LPS", "trigger_type": "lps", "amount_usd": 219, "stacking": "exclusive"},
            {"name": "UPS Fuel", "trigger_type": "fuel", "amount_usd": 0, "stacking": "chain"},
        ]
        total = calculate_total_cost(base, surcharges, FUEL_SURCHARGE_UPS)
        # subtotal = 80 + 219 = 299
        # fuel = 299 * 0.46 = 137.54
        # total = 299 + 137.54 = 436.54
        assert total == 436.54

    def test_ups_ahs_with_fuel(self):
        """UPS: base + AHS + fuel 46%."""
        base = 80
        surcharges = [
            {"name": "UPS AHS", "trigger_type": "ahs", "amount_usd": 46, "stacking": "independent"},
            {"name": "UPS Fuel", "trigger_type": "fuel", "amount_usd": 0, "stacking": "chain"},
        ]
        total = calculate_total_cost(base, surcharges, FUEL_SURCHARGE_UPS)
        # subtotal = 80 + 46 = 126
        # fuel = 126 * 0.46 = 57.96
        # total = 126 + 57.96 = 183.96
        assert total == 183.96

    def test_fedex_oversize_with_fuel(self):
        """FedEx: base + Oversize + fuel 46%."""
        base = 80
        surcharges = [
            {"name": "FedEx Oversize", "trigger_type": "oversize", "amount_usd": 255, "stacking": "exclusive"},
            {"name": "FedEx Fuel", "trigger_type": "fuel", "amount_usd": 0, "stacking": "chain"},
        ]
        total = calculate_total_cost(base, surcharges, FUEL_SURCHARGE_FEDEX)
        # subtotal = 80 + 255 = 335
        # fuel = 335 * 0.46 = 154.1
        # total = 335 + 154.1 = 489.1
        assert total == 489.1

    def test_no_surcharges_only_base_plus_fuel(self):
        """No surcharges: base + fuel only."""
        base = 100
        surcharges = [
            {"name": "DHL Fuel", "trigger_type": "fuel", "amount_usd": 0, "stacking": "chain"},
        ]
        total = calculate_total_cost(base, surcharges, FUEL_SURCHARGE_DHL)
        # 100 * 1.36 = 136
        assert total == 136.0


# ---------------------------------------------------------------------------
# Full recommendation tests
# ---------------------------------------------------------------------------

class TestGetShippingRecommendation:
    """Full shipping recommendation comparison."""

    def test_returns_three_carrier_options(self):
        """All three carriers should be evaluated."""
        box = {"length_cm": 40, "width_cm": 30, "height_cm": 20}
        result = get_shipping_recommendation(box, 5, "intl")

        assert len(result["all_carriers"]) == 3

    def test_recommendation_is_cheapest(self):
        """Recommended carrier should be the cheapest option."""
        box = {"length_cm": 30, "width_cm": 20, "height_cm": 15}
        result = get_shipping_recommendation(box, 5, 6)

        if result["recommended"] and result["alternatives"]:
            rec_cost = result["recommended"]["cost_usd"]
            for alt in result["alternatives"]:
                assert rec_cost <= alt["cost_usd"]

    def test_heavier_package_higher_cost(self):
        """Heavier packages should result in higher shipping costs."""
        box = {"length_cm": 30, "width_cm": 20, "height_cm": 15}

        result_light = get_shipping_recommendation(box, 5, 6)
        result_heavy = get_shipping_recommendation(box, 30, 6)

        light_cost = sum(c["cost_usd"] for c in result_light["all_carriers"])
        heavy_cost = sum(c["cost_usd"] for c in result_heavy["all_carriers"])
        assert heavy_cost > light_cost

    def test_large_dim_weight_drives_cost(self):
        """Large, light box should have dim_weight > actual → drives billable weight."""
        box = {"length_cm": 100, "width_cm": 80, "height_cm": 60}
        result = get_shipping_recommendation(box, 2, 6)

        # For most carriers, dim_weight should be much higher than 2kg
        for carrier in result["all_carriers"]:
            assert carrier["dim_weight_kg"] > carrier["actual_weight_kg"]
            assert carrier["billable_weight_kg"] == carrier["dim_weight_kg"]

    def test_result_contains_expected_keys(self):
        """Response should contain all expected keys."""
        box = {"length_cm": 40, "width_cm": 30, "height_cm": 20}
        result = get_shipping_recommendation(box, 5, 6)

        assert "recommended" in result
        assert "alternatives" in result
        assert "all_carriers" in result
        assert "total_weight_kg" in result
        assert "box_dimensions" in result
        assert "dim_weight_metric_kg" in result
        assert "dim_weight_imperial_kg" in result

    def test_carrier_detail_has_all_fields(self):
        """Each carrier result should have complete detail fields."""
        box = {"length_cm": 40, "width_cm": 30, "height_cm": 20}
        result = get_shipping_recommendation(box, 5, 6)

        for carrier in result["all_carriers"]:
            assert "carrier" in carrier
            assert "cost_usd" in carrier
            assert "base_rate_usd" in carrier
            assert "billable_weight_kg" in carrier
            assert "dim_weight_kg" in carrier
            assert "actual_weight_kg" in carrier
            assert "surcharges" in carrier
            assert "zone" in carrier
            assert "estimated_days" in carrier

    def test_different_zones_different_rates(self):
        """Different zones should produce different rate comparisons."""
        box = {"length_cm": 30, "width_cm": 20, "height_cm": 15}

        result_z5 = get_shipping_recommendation(box, 5, 5)
        result_z8 = get_shipping_recommendation(box, 5, 8)

        # Zone 8 should be more expensive than zone 5 for UPS/FedEx
        ups_z5 = next((c for c in result_z5["all_carriers"] if c["carrier"] == "UPS"), None)
        ups_z8 = next((c for c in result_z8["all_carriers"] if c["carrier"] == "UPS"), None)
        if ups_z5 and ups_z8:
            assert ups_z8["cost_usd"] > ups_z5["cost_usd"]


# ---------------------------------------------------------------------------
# 2026 cubic trigger tests
# ---------------------------------------------------------------------------

class TestCubicVolumeTriggers:
    """2026 UPS/FedEx cubic volume trigger thresholds."""

    def test_volume_10368_triggers_ahs(self):
        """Volume > 10368in³ triggers AHS for UPS and FedEx."""
        # 18*18*32 = 10368in³ → exactly at threshold
        # Need slightly above: 18*18*33 = 10692in³
        L_cm = 18 / CM_TO_IN * 33 / 32  # scale to just above threshold
        # Actually let's just use inches directly in surcharges
        # 50cm*50cm*50cm ≈ 19.7*19.7*19.7in → volume ≈ 7685in³ (below 10368)
        # 100cm*50cm*50cm ≈ 39.4*19.7*19.7 → volume ≈ 15367in³ (above 10368)
        surcharges_ups = calculate_surcharges("UPS", 100, 50, 50, 10)
        ahs = [s for s in surcharges_ups if s["trigger_type"] == "ahs"]
        assert len(ahs) >= 1  # volume triggers AHS

    def test_volume_17280_triggers_fedex_oversize(self):
        """Volume > 17280in³ triggers FedEx Oversize (replaces AHS)."""
        # 100cm*80cm*60cm ≈ 39*31*24in → volume ≈ 29,184in³ > 17280
        surcharges_fedex = calculate_surcharges("FedEx", 100, 80, 60, 10)
        oversize = [s for s in surcharges_fedex if s["trigger_type"] == "oversize"]
        assert len(oversize) >= 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases for shipping calculation."""

    def test_zero_weight(self):
        """Zero weight should still compute based on dim weight."""
        box = {"length_cm": 40, "width_cm": 30, "height_cm": 20}
        result = get_shipping_recommendation(box, 0, 6)
        # Billable weight should be dim_weight (since actual=0)
        for carrier in result["all_carriers"]:
            assert carrier["billable_weight_kg"] > 0

    def test_very_small_package(self):
        """Very small package → actual weight likely dominates."""
        box = {"length_cm": 10, "width_cm": 8, "height_cm": 5}
        result = get_shipping_recommendation(box, 5, 6)
        # 10*8*5/5000 = 0.08kg dim_weight, actual=5 → billable=5
        for carrier in result["all_carriers"]:
            assert carrier["billable_weight_kg"] == 5.0

    def test_girth_calculation(self):
        """Girth = 2*(W+H) in inches."""
        # length=30, width=20, height=15 → girth = 2*(20+15) = 70in
        girth = calculate_girth_in(30, 20, 15)
        assert girth == 70
