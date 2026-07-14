"""Evidence-based assembly classification for sign products."""

from __future__ import annotations


def _contains(value: object, *words: str) -> bool:
    text = str(value or "").lower()
    return any(word.lower() in text for word in words)


def classify_assembly(sheet: dict | None, metadata: dict, components: list[dict]) -> dict:
    fields = (sheet or {}).get("fields", {})
    style = fields.get("style_type")
    detachable = fields.get("detachable")
    foot = fields.get("installation_foot")
    confirmed = [item for item in components if item["confirmed_packing_outline"]]
    evidence = []

    if _contains(style, "灯箱"):
        classification = "integrated"
        confidence = 0.95
        evidence.append({"source": "production_sheet", "fact": "灯箱工艺形成整体刚性主体"})
        if _contains(detachable, "是", "yes") and foot:
            classification = "mixed"
            confidence = 0.98
            evidence.append({
                "source": "production_sheet",
                "fact": "安装脚标记为可拆；主体保持整体，安装脚作为独立附件",
            })
    elif _contains(style, "背光字", "发光字"):
        classification = "separable"
        confidence = 0.85
        evidence.append({"source": "production_sheet", "fact": "独立字壳工艺通常按字形成装箱实体"})
    elif confirmed:
        classification = "integrated" if len(confirmed) == 1 else "separable"
        confidence = 0.9
        evidence.append({"source": "svg", "fact": f"检测到 {len(confirmed)} 个已命名 PACK 轮廓"})
    else:
        classification = "needs_review"
        confidence = 0.35
        evidence.append({
            "source": "cdr",
            "fact": "缺少生产工艺或已确认 PACK_OUTLINE，不能由可见曲线数量推断实体数量",
        })

    if not confirmed:
        evidence.append({"source": "svg", "fact": "几何轮廓尚未由操作员确认"})
    if metadata.get("layer_names") and all(name in {"图层 1", "Layer 1"} for name in metadata["layer_names"]):
        evidence.append({"source": "cdr", "fact": "CDR 只有通用图层，group/曲线不得直接计件"})

    requires_review = classification == "needs_review" or not confirmed
    return {
        "classification": classification,
        "confidence": confidence,
        "requires_review": requires_review,
        "evidence": evidence,
    }


def build_packing_units(classification: dict, sheet: dict | None, components: list[dict]) -> list[dict]:
    """Build editable draft units only from explicitly confirmed SVG outlines."""
    fields = (sheet or {}).get("fields", {})
    confirmed = [item for item in components if item["confirmed_packing_outline"]]
    units = []
    for index, component in enumerate(confirmed, start=1):
        identifier = component["component_id"]
        lower = identifier.lower()
        role = "integrated_body"
        if "foot" in lower or "脚" in identifier:
            role = "detachable_accessory"
        elif classification["classification"] in {"separable", "mixed"}:
            role = "independent_letter"
        units.append({
            "unit_id": identifier,
            "name": identifier.removeprefix("pack-") or f"component-{index}",
            "role": role,
            "polygons": component["polygons"],
            "quantity": fields.get("quantity") or 1,
            "thickness_cm": None,
            "weight_kg": None,
            "stackable": False,
            "requires_dimensions": True,
        })
    return units
