"""Irregular 2D packing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from app.schemas_irregular import IrregularSolveRequest
from app.services.irregular.inspection import inspect_irregular_files
from app.services.irregular.optimizer import optimize_irregular_order
from app.services.irregular.visualization import generate_irregular_html
from app.services.public_shipping import QuoteUnavailable


router = APIRouter(prefix="/pack/irregular-2d", tags=["irregular-2d"])


@router.post("/inspect")
async def inspect_irregular(
    cdr_file: UploadFile = File(...),
    production_sheet: UploadFile | None = File(default=None),
    reference_image: UploadFile | None = File(default=None),
    svg_file: UploadFile | None = File(default=None),
):
    """Inspect source files and classify rigid bodies, accessories, and views."""
    try:
        return inspect_irregular_files(
            cdr_name=cdr_file.filename or "source.cdr",
            cdr_bytes=await cdr_file.read(),
            production_sheet_name=production_sheet.filename if production_sheet else None,
            production_sheet_bytes=await production_sheet.read() if production_sheet else None,
            reference_image_name=reference_image.filename if reference_image else None,
            reference_image_bytes=await reference_image.read() if reference_image else None,
            svg_name=svg_file.filename if svg_file else None,
            svg_bytes=await svg_file.read() if svg_file else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/solve")
def solve_irregular(request: IrregularSolveRequest):
    """Optimize confirmed irregular units and price them on the HK-SG lane."""
    try:
        return optimize_irregular_order(request)
    except (QuoteUnavailable, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/viz", response_class=HTMLResponse)
def visualize_irregular(request: IrregularSolveRequest):
    """Render the selected irregular packing result as layered SVG/HTML."""
    try:
        result = optimize_irregular_order(request)
        return HTMLResponse(generate_irregular_html(result))
    except (QuoteUnavailable, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
