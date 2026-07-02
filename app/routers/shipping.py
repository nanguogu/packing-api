"""Public shipping quotation API."""

from fastapi import APIRouter, HTTPException

from app.schemas import ShippingQuoteRequest
from app.services.public_shipping import QuoteUnavailable, quote_public_shipment


router = APIRouter(prefix="/shipping", tags=["shipping"])


@router.post("/quote")
def quote_shipping(request: ShippingQuoteRequest):
    """Compare a multi-carton Hong Kong export shipment across three carriers."""
    try:
        return quote_public_shipment(request)
    except QuoteUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
