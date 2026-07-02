"""Pydantic schemas for request validation and response serialization."""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


# --- Pack schemas --- #

class PackItem(BaseModel):
    """A single item to pack, with physical dimensions."""
    sku: str = Field(..., max_length=64, description="Product SKU identifier")
    length_cm: float = Field(..., gt=0, description="Product length in cm")
    width_cm: float = Field(..., gt=0, description="Product width in cm")
    height_cm: float = Field(..., gt=0, description="Product height in cm")
    weight_kg: float = Field(default=0, ge=0, description="Product weight in kg")
    fragile: bool = Field(default=False, description="Is this item fragile?")
    heavy: bool = Field(default=False, description="Is this item heavy?")

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "length_cm": self.length_cm,
            "width_cm": self.width_cm,
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "fragile": self.fragile,
            "heavy": self.heavy,
        }


class GroupRuleInput(BaseModel):
    """A grouping constraint rule."""
    rule_type: str = Field(..., description="must_pack_together / must_not_pack_together / pack_near")
    source_sku: str = Field(..., description="Source product SKU")
    target_sku: str = Field(..., description="Target product SKU")
    priority: int = Field(default=1, description="Priority: 0=P0, 1=P1, 2=P2")
    description: str | None = Field(default=None)

    def to_dict(self) -> dict:
        return {
            "rule_type": self.rule_type,
            "source_sku": self.source_sku,
            "target_sku": self.target_sku,
            "priority": self.priority,
            "description": self.description,
        }


class ShippingLimits(BaseModel):
    """Shipping transport limits (P0 constraints)."""
    max_length_cm: float | None = Field(default=None, description="Max box length in cm")
    max_width_cm: float | None = Field(default=None, description="Max box width in cm")
    max_height_cm: float | None = Field(default=None, description="Max box height in cm")
    max_weight_kg: float | None = Field(default=None, description="Max total weight in kg")

    def to_dict(self) -> dict:
        d = {}
        if self.max_length_cm is not None:
            d["max_length_cm"] = self.max_length_cm
        if self.max_width_cm is not None:
            d["max_width_cm"] = self.max_width_cm
        if self.max_height_cm is not None:
            d["max_height_cm"] = self.max_height_cm
        if self.max_weight_kg is not None:
            d["max_weight_kg"] = self.max_weight_kg
        return d


class PackDirectRequest(BaseModel):
    """Request body for /pack/direct: pack items directly without DB lookup."""
    items: list[PackItem] = Field(..., min_length=1, description="Items to pack")
    group_rules: list[GroupRuleInput] | None = Field(default=None, description="Optional grouping constraints")
    shipping_limits: ShippingLimits | None = Field(default=None, description="Optional shipping limits")
    destination: str | int = Field(default="intl", description="Shipping zone/destination")
    time_limit_s: float = Field(default=8.0, description="Solver time limit per group (seconds)")
    verify: bool = Field(default=True, description="Cross-check with py3dbp verifier")
    dual_path: bool = Field(default=False, description="Compute dual-path (路A compliant vs 路B optimal+surcharges) and recommend cheapest")


class PackRequest(BaseModel):
    """Request body for the /pack endpoint: list of SKUs to pack together."""
    skus: list[str] = Field(..., min_length=1, description="List of product SKUs to pack")
    destination: str = Field(..., description="Shipping destination region or address")
    group_rules_override: list[dict] | None = Field(
        default=None, description="Optional overrides for grouping constraints"
    )


class PackResponse(BaseModel):
    """Response from the /pack endpoint with recommended packing plan and shipping."""
    strategy: str = Field(..., description="Packing strategy used: single_box / multi_box / mixed")
    box_dimensions: dict = Field(..., description="Recommended box dimensions in cm")
    layout: dict | None = Field(default=None, description="Item placement layout within the box")
    utilization: float = Field(..., description="Space utilization percentage (0-100)")
    shipping_recommendation: dict | None = Field(default=None, description="Recommended shipping option")


# --- Product schemas --- #

class ProductCreate(BaseModel):
    """Schema for creating a single product."""
    sku: str = Field(..., max_length=64)
    name: str = Field(..., max_length=256)
    length_cm: float = Field(..., gt=0)
    width_cm: float = Field(..., gt=0)
    height_cm: float = Field(..., gt=0)
    weight_kg: float = Field(..., gt=0)
    shape_type: str = Field(default="box", max_length=32)
    fragile: bool = Field(default=False)
    heavy: bool = Field(default=False)
    tags: dict | None = Field(default=None)


class ProductRead(BaseModel):
    """Schema for reading a product (includes database id)."""
    id: int
    sku: str
    name: str
    length_cm: float
    width_cm: float
    height_cm: float
    weight_kg: float
    shape_type: str
    fragile: bool
    heavy: bool
    tags: dict | None

    model_config = {"from_attributes": True}


class ProductBatchImport(BaseModel):
    """Schema for batch CSV import: list of products to create at once."""
    products: list[ProductCreate] = Field(..., min_length=1)


# --- Public shipping quote schemas --- #

class QuotePackage(BaseModel):
    """One customer carton in a multi-piece shipment."""
    reference: str = Field(..., min_length=1, max_length=64)
    length_cm: float = Field(..., gt=0)
    width_cm: float = Field(..., gt=0)
    height_cm: float = Field(..., gt=0)
    weight_kg: float = Field(..., gt=0)


class ShippingQuoteRequest(BaseModel):
    """Quote a Hong Kong international export shipment."""
    origin: Literal["HK"] = "HK"
    destination: str = Field(..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code")
    service_type: Literal["express", "priority", "economy"] = "priority"
    packages: list[QuotePackage] = Field(..., min_length=1)
