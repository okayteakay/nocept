from pydantic import BaseModel
from datetime import date
from typing import Optional
from enum import Enum


class ExceptionType(str, Enum):
    NONE = "none"
    PRICE_VARIANCE = "price_variance"
    QUANTITY_VARIANCE = "quantity_variance"
    MISSING_GOODS_RECEIPT = "missing_goods_receipt"
    DUPLICATE_INVOICE = "duplicate_invoice"
    INFORMAL_MODIFICATION = "informal_modification"


class LineItem(BaseModel):
    sku: str
    description: str
    product_grade: str
    unit_price: float
    quantity: int
    total: float


class PurchaseOrder(BaseModel):
    po_number: str
    supplier_id: str
    supplier_name: str
    line_items: list[LineItem]
    total_amount: float
    creation_date: date
    created_by: str
    department: str
    cost_center: str


class Invoice(BaseModel):
    invoice_number: str
    po_number: str
    supplier_id: str
    supplier_name: str
    line_items: list[LineItem]
    total_amount: float
    invoice_date: date
    due_date: date
    payment_terms: str


class GoodsReceipt(BaseModel):
    gr_number: str
    po_number: str
    invoice_number: str
    supplier_id: str
    line_items: list[LineItem]
    date_received: date
    received_by: str
    notes: Optional[str] = None


class Supplier(BaseModel):
    supplier_id: str
    name: str
    contact_person: str
    contact_email: str
    phone: str
    category: str


class Email(BaseModel):
    email_id: str
    subject: str
    sender: str
    receiver: str
    date: date
    body: str
    related_po: Optional[str] = None
    related_invoice: Optional[str] = None


class PhoneTranscript(BaseModel):
    transcript_id: str
    caller: str
    caller_organization: str
    callee: str
    callee_organization: str
    date: date
    duration_minutes: int
    transcript: str
    related_po: Optional[str] = None
    related_invoice: Optional[str] = None


class ExceptionRecord(BaseModel):
    exception_id: str
    po_number: str
    invoice_number: str
    supplier_id: str
    exception_type: ExceptionType
    variance_amount: float
    variance_percentage: float
    description: str
    related_email_ids: list[str] = []
    related_transcript_ids: list[str] = []
