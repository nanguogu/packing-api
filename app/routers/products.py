"""Product CRUD router.

Provides endpoints for listing, creating, batch-importing, and querying products
by SKU. The batch import endpoint accepts either a JSON list or triggers CSV parsing.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Product
from app.schemas import ProductCreate, ProductRead, ProductBatchImport

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/", response_model=list[ProductRead])
def list_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """List all products with pagination."""
    products = db.query(Product).offset(skip).limit(limit).all()
    return products


@router.post("/", response_model=ProductRead)
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    """Create a single product entry."""
    db_product = Product(**product.model_dump())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product


@router.post("/batch", response_model=list[ProductRead])
def batch_import_products(batch: ProductBatchImport, db: Session = Depends(get_db)):
    """Batch create products from a JSON list."""
    created = []
    for item in batch.products:
        db_product = Product(**item.model_dump())
        db.add(db_product)
        created.append(db_product)
    db.commit()
    for p in created:
        db.refresh(p)
    return created


@router.post("/import-csv", response_model=list[ProductRead])
def import_csv_products(db: Session = Depends(get_db)):
    """Trigger CSV product import from the configured data directory.

    This endpoint reads the latest CSV file and bulk-inserts products.
    TODO: Implement CSV file detection and pandas-based parsing.
    """
    raise HTTPException(status_code=501, detail="CSV import not yet implemented")


@router.get("/{sku}", response_model=ProductRead)
def get_product_by_sku(sku: str, db: Session = Depends(get_db)):
    """Retrieve a single product by its SKU identifier."""
    product = db.query(Product).filter(Product.sku == sku).first()
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product with SKU '{sku}' not found")
    return product
