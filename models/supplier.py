"""Supplier and product catalog models.

The catalog (dataset/catalog.json) contains suppliers with their full product
hierarchy: supplier → product categories → grades (each grade is a distinct SKU
with its own price). The classifier uses this to detect known substitutions —
when an invoiced SKU belongs to the same product category as the PO SKU.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ProductGrade(BaseModel):
    """A single grade/tier of a product (e.g. Standard, Premium, Ultra)."""

    sku: str
    description: str
    grade: str
    unit_price: float


class ProductCategory(BaseModel):
    """A product category containing multiple grade variants."""

    product_category: str
    grades: list[ProductGrade]

    def grade_by_sku(self, sku: str) -> ProductGrade | None:
        """Return the grade entry matching the given SKU, or None."""
        return next((g for g in self.grades if g.sku == sku), None)

    def contains_sku(self, sku: str) -> bool:
        """Return True if this category includes the given SKU."""
        return any(g.sku == sku for g in self.grades)

    def is_higher_grade(self, from_sku: str, to_sku: str) -> bool:
        """Return True if to_sku is a higher-priced grade than from_sku in this category."""
        from_grade = self.grade_by_sku(from_sku)
        to_grade = self.grade_by_sku(to_sku)
        if from_grade is None or to_grade is None:
            return False
        return to_grade.unit_price > from_grade.unit_price

    def price_delta_pct(self, from_sku: str, to_sku: str) -> float | None:
        """Return (to_price - from_price) / from_price, or None if either SKU missing."""
        from_grade = self.grade_by_sku(from_sku)
        to_grade = self.grade_by_sku(to_sku)
        if from_grade is None or to_grade is None or from_grade.unit_price == 0:
            return None
        return (to_grade.unit_price - from_grade.unit_price) / from_grade.unit_price


class Supplier(BaseModel):
    """Lightweight supplier record without catalog (from suppliers.json)."""

    supplier_id: str
    name: str
    contact_person: str
    contact_email: str
    phone: str
    category: str


class SupplierWithCatalog(BaseModel):
    """Full supplier entry from catalog.json, including product hierarchy."""

    supplier_id: str
    name: str
    contact_person: str
    contact_email: str
    phone: str
    category: str
    products: list[ProductCategory]

    def find_product_category(self, sku: str) -> ProductCategory | None:
        """Return the product category that contains the given SKU, or None."""
        return next((cat for cat in self.products if cat.contains_sku(sku)), None)

    def is_known_substitute(self, po_sku: str, invoice_sku: str) -> bool:
        """Return True if invoice_sku and po_sku belong to the same product category.

        Used to detect informal product substitutions: if the supplier swapped
        Grade A for Grade B within the same category, this returns True even if
        no PO amendment was filed.
        """
        po_cat = self.find_product_category(po_sku)
        inv_cat = self.find_product_category(invoice_sku)
        if po_cat is None or inv_cat is None:
            return False
        return po_cat.product_category == inv_cat.product_category

    def catalog_price_delta_pct(self, po_sku: str, invoice_sku: str) -> float | None:
        """Return the catalog-listed price delta between po_sku and invoice_sku.

        Used by the rules engine to verify that an invoice price uplift is
        consistent with the published price difference between the two grades.
        Returns None if either SKU is not in the catalog.
        """
        cat = self.find_product_category(po_sku)
        if cat is None:
            return None
        return cat.price_delta_pct(po_sku, invoice_sku)


class Buyer(BaseModel):
    """An internal Meridian Corp buyer who issues purchase orders."""

    name: str
    email: str
    department: str
    cost_center: str


class Catalog(BaseModel):
    """The master product and supplier catalog for Meridian Corp."""

    company_name: str
    buyers: list[Buyer]
    warehouse_receivers: list[str]
    suppliers: list[SupplierWithCatalog]

    def supplier_by_id(self, supplier_id: str) -> SupplierWithCatalog | None:
        """Return the catalog entry for a supplier, or None."""
        return next((s for s in self.suppliers if s.supplier_id == supplier_id), None)

    def find_product_category(
        self, supplier_id: str, sku: str
    ) -> ProductCategory | None:
        """Return the product category for a SKU at the given supplier, or None."""
        supplier = self.supplier_by_id(supplier_id)
        if supplier is None:
            return None
        return supplier.find_product_category(sku)

    def is_known_substitute(
        self, supplier_id: str, po_sku: str, invoice_sku: str
    ) -> bool:
        """Return True if invoice_sku is a known substitute for po_sku at this supplier."""
        supplier = self.supplier_by_id(supplier_id)
        if supplier is None:
            return False
        return supplier.is_known_substitute(po_sku, invoice_sku)

    def catalog_price_delta_pct(
        self, supplier_id: str, po_sku: str, invoice_sku: str
    ) -> float | None:
        """Return catalog-listed price delta between po_sku and invoice_sku at this supplier."""
        supplier = self.supplier_by_id(supplier_id)
        if supplier is None:
            return None
        return supplier.catalog_price_delta_pct(po_sku, invoice_sku)


def load_catalog(path: str | Path) -> Catalog:
    """Load and validate the catalog JSON file.

    Args:
        path: Path to catalog.json (typically dataset/catalog.json).

    Returns:
        A validated Catalog instance.
    """
    with open(path) as f:
        data = json.load(f)
    return Catalog.model_validate(data)
