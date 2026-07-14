"""Orchestrate CDR metadata, production facts, SVG geometry and classification."""

from __future__ import annotations

import hashlib

from .cdr_converter import obtain_svg
from .cdr_metadata import parse_cdr_metadata
from .classifier import build_packing_units, classify_assembly
from .geometry import parse_svg_components
from .production_sheet import parse_production_sheet


def inspect_irregular_files(
    *, cdr_name: str, cdr_bytes: bytes,
    production_sheet_name: str | None = None,
    production_sheet_bytes: bytes | None = None,
    reference_image_name: str | None = None,
    reference_image_bytes: bytes | None = None,
    svg_name: str | None = None,
    svg_bytes: bytes | None = None,
) -> dict:
    metadata = parse_cdr_metadata(cdr_bytes, cdr_name)
    sheet = (
        parse_production_sheet(production_sheet_bytes, production_sheet_name)
        if production_sheet_bytes else None
    )
    normalized_svg, conversion = obtain_svg(cdr_bytes, cdr_name, svg_bytes)
    components = (
        parse_svg_components(normalized_svg, confirmed_only=True)
        if normalized_svg else []
    )
    classification = classify_assembly(sheet, metadata, components)
    units = build_packing_units(classification, sheet, components)
    warnings = []
    if conversion["status"] != "ready":
        warnings.append(conversion["message"])
    if classification["requires_review"]:
        warnings.append("Packing units require human confirmation before solve.")
    if not units:
        warnings.append("No confirmed pack-* SVG outlines were found.")

    image_asset = None
    if reference_image_bytes:
        image_asset = {
            "filename": reference_image_name,
            "sha256": hashlib.sha256(reference_image_bytes).hexdigest(),
            "size_bytes": len(reference_image_bytes),
        }
    return {
        "status": "ready_for_dimensions" if units and not classification["requires_review"] else "needs_review",
        "source": metadata,
        "production_sheet": sheet,
        "reference_image": image_asset,
        "conversion": {**conversion, "svg_filename": svg_name},
        "assembly": classification,
        "components": components,
        "packing_units": units,
        "warnings": warnings,
    }
