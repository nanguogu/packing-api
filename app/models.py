"""SQLAlchemy ORM models for the packing optimization system."""

from sqlalchemy import Column, Integer, String, Float, Boolean, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Product(Base):
    """Product catalog item with physical dimensions and packing attributes."""
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    length_cm: Mapped[float] = mapped_column(Float, nullable=False)
    width_cm: Mapped[float] = mapped_column(Float, nullable=False)
    height_cm: Mapped[float] = mapped_column(Float, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    shape_type: Mapped[str] = mapped_column(String(32), default="box")
    fragile: Mapped[bool] = mapped_column(Boolean, default=False)
    heavy: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class GroupRule(Base):
    """Constraint rule that enforces packing grouping relationships between products."""
    __tablename__ = "group_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)  # must_pack_together / must_not_pack_together / pack_near
    source_sku: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class PackResult(Base):
    """Stored result of a packing optimization run."""
    __tablename__ = "pack_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)  # single_box / multi_box / mixed
    box_length_cm: Mapped[float] = mapped_column(Float, nullable=False)
    box_width_cm: Mapped[float] = mapped_column(Float, nullable=False)
    box_height_cm: Mapped[float] = mapped_column(Float, nullable=False)
    space_utilization: Mapped[float] = mapped_column(Float, nullable=False)
    pack_layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    shipping_recommendation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
