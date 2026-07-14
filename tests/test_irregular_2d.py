"""Irregular sign import, classification, geometry and cost optimization tests."""

from __future__ import annotations

from app.schemas_irregular import IrregularSolveRequest
from app.services.irregular.classifier import classify_assembly
from app.services.irregular.geometry import parse_svg_components
from app.services.irregular.optimizer import optimize_irregular_order
from app.services.irregular.verifier import verify_plan


def _unit(identifier: str, width: float, height: float, **overrides):
    value = {
        "unit_id": identifier, "name": identifier, "role": "independent_letter",
        "polygons": [{"outer": [[0, 0], [width, 0], [width, height], [0, height]]}],
        "thickness_cm": 1, "weight_kg": 1,
    }
    value.update(overrides)
    return value


def test_svg_requires_pack_prefix_for_confirmed_geometry():
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
      <rect id="dimension-line" x="0" y="0" width="10" height="10"/>
      <path id="pack-letter-r" d="M 20 0 L 100 0 L 100 50 L 20 50 Z"/>
    </svg>'''
    components = parse_svg_components(svg)
    assert len(components) == 2
    confirmed = [item for item in components if item["confirmed_packing_outline"]]
    assert [item["component_id"] for item in confirmed] == ["pack-letter-r"]
    assert abs(confirmed[0]["bounds_cm"]["width"] - 8) < 0.01


def test_dw_lightbox_is_body_plus_detachable_foot():
    sheet = {"fields": {"style_type": "双面面光灯箱", "installation_foot": "不锈钢安装脚", "detachable": "是"}}
    result = classify_assembly(sheet, {"layer_names": ["图层 1"]}, [])
    assert result["classification"] == "mixed"
    assert result["requires_review"] is True
    assert any("安装脚" in item["fact"] for item in result["evidence"])


def test_backlit_letters_default_to_separable_but_require_outline_review():
    sheet = {"fields": {"style_type": "普通背光字"}}
    result = classify_assembly(sheet, {}, [])
    assert result["classification"] == "separable"
    assert result["requires_review"] is True


def test_optimizer_uses_singapore_rate_card_and_labels_fallback():
    request = IrregularSolveRequest.model_validate({
        "order_id": "IR-001", "requested_destination": "US",
        "units": [_unit("R", 10, 5), _unit("E", 6, 4)],
        "packing": {"rotation_step_deg": 90},
    })
    result = optimize_irregular_order(request)
    assert result["requested_destination"] == "US"
    assert result["pricing_destination"] == "SG"
    assert result["pricing_lane_used"] == "HK-SG"
    assert result["shipping"]["destination"] == "SG"
    assert result["cost_scope"] == "shipping_only"
    assert result["solver"]["name"] == "python_baseline"
    assert result["solver"]["fallback"] is True
    assert result["verification"]["valid"] is True


def test_multi_layer_only_when_all_units_are_stackable():
    request = IrregularSolveRequest.model_validate({
        "order_id": "IR-STACK", "units": [
            _unit("A", 8, 8, stackable=True), _unit("B", 8, 8, stackable=True),
        ],
        "packing": {"rotation_step_deg": 90, "max_layers": 2},
    })
    result = optimize_irregular_order(request)
    assert result["carton"]["layer_count"] in {1, 2}
    assert any(item["layer_count"] == 2 for item in result["alternatives"]) or result["carton"]["layer_count"] == 2


def test_configured_costs_are_added_to_shipping():
    request = IrregularSolveRequest.model_validate({
        "order_id": "IR-COST", "units": [_unit("A", 10, 10)],
        "packing": {"rotation_step_deg": 90},
        "packaging_cost_hkd": 10, "labor_cost_hkd": 20,
        "material_cost_hkd": 30, "risk_cost_hkd": 40,
    })
    result = optimize_irregular_order(request)
    assert result["cost_scope"] == "full_configured_total_cost"
    assert result["costs_hkd"]["configured_additional_total"] == 100
    assert result["costs_hkd"]["selected_total"] == result["costs_hkd"]["shipping"] + 100
