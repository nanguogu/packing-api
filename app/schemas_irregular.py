"""Request schemas for irregular 2D packing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class IrregularPolygon(BaseModel):
    """A polygon in centimetres, including optional physical holes."""

    outer: list[tuple[float, float]] = Field(..., min_length=3)
    holes: list[list[tuple[float, float]]] = Field(default_factory=list)


class IrregularUnit(BaseModel):
    """One independently movable rigid packing unit."""

    unit_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    role: Literal[
        "integrated_body",
        "independent_letter",
        "rigid_logo",
        "detachable_accessory",
        "accessory_pack",
    ]
    polygons: list[IrregularPolygon] = Field(..., min_length=1)
    quantity: int = Field(default=1, ge=1, le=500)
    thickness_cm: float = Field(..., gt=0)
    weight_kg: float = Field(..., gt=0)
    stackable: bool = False
    allow_hole_nesting: bool = True
    allowed_rotations_deg: list[float] | None = None


class IrregularPackingConfig(BaseModel):
    """Physical and solver settings for an irregular shipment."""

    item_clearance_cm: float = Field(default=1.5, ge=0, le=20)
    edge_margin_cm: float = Field(default=2.0, ge=0, le=20)
    top_padding_cm: float = Field(default=2.0, ge=0, le=30)
    bottom_padding_cm: float = Field(default=2.0, ge=0, le=30)
    interlayer_padding_cm: float = Field(default=1.0, ge=0, le=30)
    wall_allowance_cm: float = Field(default=1.0, ge=0, le=20)
    rotation_step_deg: float = Field(default=5.0, gt=0, le=90)
    allow_mirror: bool = False
    part_in_part: bool = True
    max_layers: int = Field(default=1, ge=1, le=10)
    max_inner_width_cm: float | None = Field(default=None, gt=0)
    max_inner_height_cm: float | None = Field(default=None, gt=0)
    time_limit_s: float = Field(default=15.0, gt=0, le=120)
    seed: int = 1


class IrregularSolveRequest(BaseModel):
    """Cost-optimize confirmed irregular units using the SG rate-card lane."""

    order_id: str = Field(..., min_length=1, max_length=64)
    requested_destination: str = Field(default="SG", min_length=2, max_length=2)
    pricing_destination: Literal["SG"] = "SG"
    service_type: Literal["priority"] = "priority"
    units: list[IrregularUnit] = Field(..., min_length=1, max_length=500)
    packing: IrregularPackingConfig = Field(default_factory=IrregularPackingConfig)
    packaging_type: Literal["carton", "wooden_crate"] = "wooden_crate"
    packaging_weight_kg: float = Field(default=0.0, ge=0)
    packaging_cost_hkd: float | None = Field(default=None, ge=0)
    labor_cost_hkd: float | None = Field(default=None, ge=0)
    material_cost_hkd: float | None = Field(default=None, ge=0)
    risk_cost_hkd: float | None = Field(default=None, ge=0)
    objective: Literal["minimum_total_cost"] = "minimum_total_cost"

    @model_validator(mode="after")
    def validate_unique_units(self):
        ids = [unit.unit_id for unit in self.units]
        if len(ids) != len(set(ids)):
            raise ValueError("unit_id values must be unique")
        return self
