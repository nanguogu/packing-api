"""CSV product data import script.

Reads product data from a CSV file and bulk-inserts into the PostgreSQL
database. Supports flexible column mapping, duplicate SKU detection,
and dry-run mode.

Expected CSV columns (flexible, auto-mapped):
  sku, name, length_cm, width_cm, height_cm, weight_kg,
  shape_type, fragile, heavy

CSV can also use alternative column names:
  sku → SKU / product_id / product_code
  name → product_name / description
  length_cm → length / L / l_cm
  width_cm → width / W / w_cm
  height_cm → height / H / h_cm
  weight_kg → weight / wt / w_kg

Usage:
    python scripts/import_products.py --file data/products.csv
    python scripts/import_products.py --file data/products.csv --dry-run
    python scripts/import_products.py --file data/products.csv --skip-duplicates
"""

from __future__ import annotations

import argparse
import csv
import sys
import os
import logging

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Product

logger = logging.getLogger(__name__)

# Column name mapping: alternative names → standard field names
COLUMN_MAP = {
    "sku": "sku",
    "SKU": "sku",
    "product_id": "sku",
    "product_code": "sku",
    "item_id": "sku",
    "name": "name",
    "product_name": "name",
    "description": "name",
    "length_cm": "length_cm",
    "length": "length_cm",
    "L": "length_cm",
    "l_cm": "length_cm",
    "width_cm": "width_cm",
    "width": "width_cm",
    "W": "width_cm",
    "w_cm": "width_cm",
    "height_cm": "height_cm",
    "height": "height_cm",
    "H": "height_cm",
    "h_cm": "height_cm",
    "weight_kg": "weight_kg",
    "weight": "weight_kg",
    "wt": "weight_kg",
    "w_kg": "weight_kg",
    "shape_type": "shape_type",
    "shape": "shape_type",
    "fragile": "fragile",
    "heavy": "heavy",
}

# Required columns (must have at least one mapping for each)
REQUIRED_FIELDS = ["sku", "name", "length_cm", "width_cm", "height_cm", "weight_kg"]


def normalize_columns(headers: list[str]) -> dict[str, str]:
    """Map CSV column headers to standard field names.

    Args:
        headers: List of column header strings from the CSV.

    Returns:
        Dict mapping CSV column name → standard field name.
        Only includes columns that have a known mapping.
    """
    mapping = {}
    for h in headers:
        h_clean = h.strip()
        if h_clean in COLUMN_MAP:
            mapping[h_clean] = COLUMN_MAP[h_clean]
        else:
            logger.warning(f"Unknown column: '{h_clean}' — will be ignored")
    return mapping


def validate_mapping(mapping: dict[str, str]) -> list[str]:
    """Check that all required fields are mapped.

    Args:
        mapping: Dict from normalize_columns.

    Returns:
        List of missing required field names.
    """
    mapped_fields = set(mapping.values())
    missing = [f for f in REQUIRED_FIELDS if f not in mapped_fields]
    return missing


def parse_csv(filepath: str) -> list[dict]:
    """Read and parse a CSV file into a list of product dicts.

    Args:
        filepath: Path to the CSV file.

    Returns:
        List of dicts with standard field names.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        mapping = normalize_columns(headers)
        missing = validate_mapping(mapping)
        if missing:
            logger.error(f"Missing required fields in CSV: {missing}")
            logger.error(f"Available columns: {headers}")
            raise ValueError(f"CSV missing columns for required fields: {missing}")

        products = []
        for row in reader:
            product_dict = {}
            for csv_col, field_name in mapping.items():
                value = row.get(csv_col, "").strip()
                if not value:
                    continue

                # Type conversion
                if field_name in ("length_cm", "width_cm", "height_cm", "weight_kg"):
                    try:
                        product_dict[field_name] = float(value)
                    except ValueError:
                        logger.error(f"Row {reader.line_num}: invalid numeric value '{value}' for {field_name}")
                        product_dict[field_name] = 0.0
                elif field_name in ("fragile", "heavy"):
                    product_dict[field_name] = value.lower() in ("true", "1", "yes", "y")
                elif field_name == "name" and not value:
                    product_dict[field_name] = product_dict.get("sku", "unknown")
                else:
                    product_dict[field_name] = value

            # Default values for optional fields
            product_dict.setdefault("name", product_dict.get("sku", "unknown"))
            product_dict.setdefault("shape_type", "box")
            product_dict.setdefault("fragile", False)
            product_dict.setdefault("heavy", False)

            # Validate required numeric fields > 0
            for dim_field in ("length_cm", "width_cm", "height_cm"):
                if product_dict.get(dim_field, 0) <= 0:
                    logger.error(f"Row {reader.line_num}: {dim_field} must be > 0")
                    continue

            if product_dict.get("weight_kg", 0) < 0:
                logger.error(f"Row {reader.line_num}: weight_kg must be >= 0")
                continue

            products.append(product_dict)

    logger.info(f"Parsed {len(products)} products from {filepath}")
    return products


def import_products(
    products: list[dict],
    skip_duplicates: bool = True,
    dry_run: bool = False,
) -> dict:
    """Insert parsed products into the database.

    Args:
        products: List of product dicts with standard field names.
        skip_duplicates: If True, skip SKUs that already exist. If False, raise error.
        dry_run: If True, validate without inserting.

    Returns:
        Dict with import stats: inserted, skipped, errors.
    """
    inserted = 0
    skipped = 0
    errors = 0

    if dry_run:
        logger.info("DRY RUN — no database writes")
        for p in products:
            # Validation only
            if not p.get("sku"):
                errors += 1
                continue
            for f in REQUIRED_FIELDS:
                if f not in p:
                    errors += 1
                    break
        return {"inserted": 0, "skipped": 0, "errors": errors, "dry_run": True}

    db = SessionLocal()
    try:
        existing_skus = {p.sku for p in db.query(Product.sku).all()}

        for p in products:
            sku = p.get("sku")
            if not sku:
                errors += 1
                logger.error(f"Missing SKU in product data")
                continue

            if sku in existing_skus:
                if skip_duplicates:
                    skipped += 1
                    logger.debug(f"Skip duplicate SKU: {sku}")
                    continue
                else:
                    errors += 1
                    logger.error(f"Duplicate SKU: {sku}")
                    continue

            try:
                db_product = Product(**p)
                db.add(db_product)
                inserted += 1
                existing_skus.add(sku)
            except Exception as e:
                errors += 1
                logger.error(f"Error inserting {sku}: {e}")
                db.rollback()

        db.commit()
    finally:
        db.close()

    return {"inserted": inserted, "skipped": skipped, "errors": errors, "dry_run": False}


def main():
    """Parse CLI arguments and run the CSV import process."""
    parser = argparse.ArgumentParser(description="Import products from CSV into the database")
    parser.add_argument("--file", required=True, help="Path to the CSV file to import")
    parser.add_argument("--dry-run", action="store_true", help="Validate without inserting")
    parser.add_argument("--skip-duplicates", action="store_true", default=True,
                        help="Skip SKUs that already exist (default: True)")
    parser.add_argument("--no-skip-duplicates", action="store_false", dest="skip_duplicates",
                        help="Raise error on duplicate SKUs instead of skipping")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Parse CSV
    try:
        products = parse_csv(args.file)
    except FileNotFoundError:
        logger.error(f"CSV file not found: {args.file}")
        sys.exit(1)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    if not products:
        logger.warning("No valid products found in CSV")
        sys.exit(0)

    # Import to database
    result = import_products(
        products,
        skip_duplicates=args.skip_duplicates,
        dry_run=args.dry_run,
    )

    # Print summary
    print(f"\nImport Summary:")
    print(f"  Total parsed: {len(products)}")
    print(f"  Inserted:     {result['inserted']}")
    print(f"  Skipped:      {result['skipped']}")
    print(f"  Errors:       {result['errors']}")
    if result["dry_run"]:
        print(f"  Mode:         DRY RUN (no database writes)")

    if result["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
