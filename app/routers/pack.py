"""Pack optimization router.

Provides the /pack endpoint that accepts a list of SKUs, applies grouping
constraints, selects optimal box sizes, verifies layout, and returns a
comprehensive packing plan with shipping recommendations.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Product, GroupRule
from app.schemas import Level1PackingRequest, PackRequest, PackResponse, PackDirectRequest
from app.services.level1 import generate_level1_guide_html, optimize_level1_order
from app.services.public_shipping import QuoteUnavailable
from app.services.packer import pack_items
from app.services.packing_list import generate_packing_list, generate_packing_list_text
from app.services.viz import generate_3d_html

from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/pack", tags=["pack"])


@router.post("/level1")
def pack_level1(request: Level1PackingRequest):
    """Optimize three cuboids into one custom carton, then compare carriers."""
    try:
        return optimize_level1_order(request)
    except (QuoteUnavailable, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/level1/viz", response_class=HTMLResponse)
def pack_level1_viz(request: Level1PackingRequest):
    """Render the Level 1 result as an item-by-item 3D packing guide."""
    try:
        result = optimize_level1_order(request)
        return HTMLResponse(content=generate_level1_guide_html(result), status_code=200)
    except (QuoteUnavailable, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _product_to_item_dict(product: Product) -> dict:
    """Convert a Product ORM object to the item dict format used by packer."""
    return {
        "sku": product.sku,
        "length_cm": product.length_cm,
        "width_cm": product.width_cm,
        "height_cm": product.height_cm,
        "weight_kg": product.weight_kg,
        "fragile": product.fragile,
        "heavy": product.heavy,
        "tags": product.tags,
    }


def _group_rule_to_dict(rule: GroupRule) -> dict:
    """Convert a GroupRule ORM object to the dict format used by grouper."""
    return {
        "rule_type": rule.rule_type,
        "source_sku": rule.source_sku,
        "target_sku": rule.target_sku,
        "priority": rule.priority,
        "description": rule.description,
    }


@router.post("/", response_model=PackResponse)
def pack_order(request: PackRequest, db: Session = Depends(get_db)):
    """Pack a list of SKUs into optimal boxes and return a packing plan.

    Args:
        request: PackRequest with SKUs, destination, and optional group rule overrides.
        db: Database session for loading product and group data.

    Returns:
        PackResponse with recommended packing strategy, box dimensions, layout,
        utilization, and shipping recommendation.
    """
    # Load products from DB by SKUs
    products = db.query(Product).filter(Product.sku.in_(request.skus)).all()
    if len(products) != len(request.skus):
        found_skus = {p.sku for p in products}
        missing = [s for s in request.skus if s not in found_skus]
        raise HTTPException(
            status_code=404,
            detail=f"Products not found: {missing}",
        )

    # Convert to item dicts
    items = [_product_to_item_dict(p) for p in products]

    # Load group rules from DB (or use override)
    if request.group_rules_override:
        rules = request.group_rules_override
    else:
        # Load rules that involve any of the requested SKUs
        rules_from_db = db.query(GroupRule).filter(
            (GroupRule.source_sku.in_(request.skus)) |
            (GroupRule.target_sku.in_(request.skus))
        ).all()
        rules = [_group_rule_to_dict(r) for r in rules_from_db]

    # Run packing pipeline
    result = pack_items(
        items=items,
        group_rules=rules,
        shipping_limits=None,
        destination=request.destination,
    )

    # Map result to PackResponse
    # For single_box strategy, use the first group's box
    # For multi_box, aggregate all groups
    if result["strategy"] == "single_box" and len(result["groups"]) == 1:
        g = result["groups"][0]
        box_dims = g["box"] or {"length_cm": 0, "width_cm": 0, "height_cm": 0}
        return PackResponse(
            strategy="single_box",
            box_dimensions=box_dims,
            layout=g["layout"],
            utilization=g["utilization"],
            shipping_recommendation=g.get("shipping"),
        )
    elif result["strategy"] == "multi_box":
        # For multi_box, return the largest group's primary box + overall info
        best_group = max(
            result["groups"],
            key=lambda g: (g["utilization"] or 0),
        )
        box_dims = best_group["box"] or {"length_cm": 0, "width_cm": 0, "height_cm": 0}
        return PackResponse(
            strategy="multi_box",
            box_dimensions=box_dims,
            layout=best_group["layout"],
            utilization=result["total_utilization"],
            shipping_recommendation=best_group.get("shipping"),
        )
    else:
        # Mixed or unknown strategy — return overall info
        return PackResponse(
            strategy=result["strategy"],
            box_dimensions={"length_cm": 0, "width_cm": 0, "height_cm": 0},
            layout=None,
            utilization=result["total_utilization"],
            shipping_recommendation=None,
        )


@router.post("/detail")
def pack_order_detail(request: PackRequest, db: Session = Depends(get_db)):
    """Pack a list of SKUs and return full detailed result (all groups).

    This endpoint returns the complete packing plan including all group
    details, verification results, and summary — useful for debugging
    and detailed analysis.

    Args:
        request: PackRequest with SKUs, destination, and optional group rule overrides.
        db: Database session for loading product and group data.

    Returns:
        Full packing result dict with all group details.
    """
    # Load products from DB by SKUs
    products = db.query(Product).filter(Product.sku.in_(request.skus)).all()
    if len(products) != len(request.skus):
        found_skus = {p.sku for p in products}
        missing = [s for s in request.skus if s not in found_skus]
        raise HTTPException(
            status_code=404,
            detail=f"Products not found: {missing}",
        )

    items = [_product_to_item_dict(p) for p in products]

    # Load group rules
    if request.group_rules_override:
        rules = request.group_rules_override
    else:
        rules_from_db = db.query(GroupRule).filter(
            (GroupRule.source_sku.in_(request.skus)) |
            (GroupRule.target_sku.in_(request.skus))
        ).all()
        rules = [_group_rule_to_dict(r) for r in rules_from_db]

    # Run full pipeline
    result = pack_items(
        items=items,
        group_rules=rules,
        shipping_limits=None,
        destination=request.destination,
    )

    return result


@router.post("/direct")
def pack_direct(request: PackDirectRequest):
    """Pack items directly without database lookup.

    This endpoint accepts items with their dimensions directly,
    making it useful for testing and for cases where product data
    is not in the database yet.

    No database dependency required — purely computational endpoint.

    Args:
        request: PackDirectRequest with items, optional group rules,
            optional shipping limits.

    Returns:
        Full packing result dict with all group details, verification,
        and summary.
    """
    items = [it.to_dict() for it in request.items]
    rules = [r.to_dict() for r in (request.group_rules or [])]
    shipping_limits = request.shipping_limits.to_dict() if request.shipping_limits else None

    result = pack_items(
        items=items,
        group_rules=rules,
        shipping_limits=shipping_limits,
        destination=request.destination,
        time_limit_s=request.time_limit_s,
        verify=request.verify,
        dual_path=request.dual_path,
    )

    return result


@router.post("/list")
def pack_list(request: PackDirectRequest):
    """Pack items and return a structured packing list (table format).

    Returns the same packing result but formatted as a packing list
    with box-level details, per-item placement info, and shipping
    summary — suitable for warehouse operations and client delivery.
    """
    items = [it.to_dict() for it in request.items]
    rules = [r.to_dict() for r in (request.group_rules or [])]
    shipping_limits = request.shipping_limits.to_dict() if request.shipping_limits else None

    pack_result = pack_items(
        items=items,
        group_rules=rules,
        shipping_limits=shipping_limits,
        destination=request.destination,
        time_limit_s=request.time_limit_s,
        verify=request.verify,
        dual_path=request.dual_path,
    )

    return generate_packing_list(pack_result)


@router.post("/list/text")
def pack_list_text(request: PackDirectRequest):
    """Pack items and return a human-readable text packing list.

    Suitable for printing, email, or plain-text export.
    """
    items = [it.to_dict() for it in request.items]
    rules = [r.to_dict() for r in (request.group_rules or [])]
    shipping_limits = request.shipping_limits.to_dict() if request.shipping_limits else None

    pack_result = pack_items(
        items=items,
        group_rules=rules,
        shipping_limits=shipping_limits,
        destination=request.destination,
        time_limit_s=request.time_limit_s,
        verify=request.verify,
        dual_path=request.dual_path,
    )

    packing_list = generate_packing_list(pack_result)
    return {"text": generate_packing_list_text(packing_list)}


@router.post("/viz", response_class=HTMLResponse)
def pack_viz(request: PackDirectRequest):
    """Pack items and return an interactive 3D visualization HTML page.

    Uses Plotly.js for cuboid rendering with step-through animation
    and keyboard shortcuts (Arrow keys, R=reset, A=show all).

    Returns:
        Standalone HTML page that can be opened in any browser.
    """
    items = [it.to_dict() for it in request.items]
    rules = [r.to_dict() for r in (request.group_rules or [])]
    shipping_limits = request.shipping_limits.to_dict() if request.shipping_limits else None

    pack_result = pack_items(
        items=items,
        group_rules=rules,
        shipping_limits=shipping_limits,
        destination=request.destination,
        time_limit_s=request.time_limit_s,
        verify=request.verify,
        dual_path=request.dual_path,
    )

    html_content = generate_3d_html(pack_result)
    return HTMLResponse(content=html_content, status_code=200)
