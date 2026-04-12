"""Shared pytest fixtures for the AP Exception Resolution Agent test suite."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import fakeredis
import pytest

from clients.redis_client import RedisStreamsClient
from clients.tavily_client import TavilyClient, TavilySearchResult
from config.settings import AppConfig
from models.communication import Email, PhoneTranscript
from models.exception import ExceptionState, InvoiceException, LineItemVariance
from models.exception_record import ExceptionRecord, ExceptionType
from models.grn import GoodsReceiptNote
from models.invoice import Invoice, LineItem
from models.purchase_order import PurchaseOrder
from models.supplier import Supplier
from state.redis_backend import RedisStateStore


# ---------------------------------------------------------------------------
# Infrastructure fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_redis():
    """A fakeredis server instance, reset between tests."""
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def store(fake_redis) -> RedisStateStore:
    """A RedisStateStore backed by fakeredis."""
    return RedisStateStore(fake_redis)


@pytest.fixture
def mock_tavily() -> TavilyClient:
    """A MagicMock TavilyClient returning empty results by default."""
    client = MagicMock(spec=TavilyClient)
    client.search.return_value = []
    client.search_supplier_context.return_value = []
    client.search_product_availability.return_value = []
    client.search_price_changes.return_value = []
    return client


@pytest.fixture
def tavily_with_results(mock_tavily) -> TavilyClient:
    """TavilyClient mock pre-loaded with canned substitution evidence."""
    result = TavilySearchResult(
        title="Apex Paper Co. Temporary Grade A Shortage",
        url="https://example.com/apex-shortage",
        content=(
            "Apex Paper Co. has announced a temporary shortage of Grade A paper. "
            "Grade B is being offered as a substitute at a 60% premium."
        ),
        score=0.92,
    )
    mock_tavily.search.return_value = [result]
    mock_tavily.search_supplier_context.return_value = [result]
    mock_tavily.search_product_availability.return_value = [result]
    return mock_tavily


@pytest.fixture
def app_config() -> AppConfig:
    """AppConfig with tight tolerances suitable for testing."""
    return AppConfig(
        REDIS_URL="redis://localhost:6379/0",
        TAVILY_API_KEY="test-key",
        PRICE_TOLERANCE_PCT=0.03,
        QTY_TOLERANCE_PCT=0.02,
    )


# ---------------------------------------------------------------------------
# Shared line items
# ---------------------------------------------------------------------------

@pytest.fixture
def grade_a_line() -> LineItem:
    """Grade A copy paper line item — 500 units @ $42."""
    return LineItem(
        sku="AP-CPA-STD",
        description="A4 Copy Paper Standard",
        product_grade="Standard",
        unit_price=42.0,
        quantity=500,
        total=21000.0,
    )


@pytest.fixture
def grade_b_line() -> LineItem:
    """Grade B (premium) copy paper line item — 50 units @ $58."""
    return LineItem(
        sku="AP-CPA-PRM",
        description="A4 Copy Paper Premium",
        product_grade="Premium",
        unit_price=58.0,
        quantity=50,
        total=2900.0,
    )


# ---------------------------------------------------------------------------
# Document fixtures — straight-through (no exception)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_po(grade_a_line) -> PurchaseOrder:
    """Clean PO: 500 units of AP-CPA-STD @ $42 = $21,000."""
    return PurchaseOrder(
        po_number="PO-TEST-001",
        supplier_id="SUP-001",
        supplier_name="Apex Paper Co.",
        created_by="buyer.johnson@meridian.com",
        department="Office Supplies",
        cost_center="CC-100",
        creation_date=date(2024, 3, 1),
        line_items=[grade_a_line],
        total_amount=21000.0,
    )


@pytest.fixture
def sample_invoice(grade_a_line) -> Invoice:
    """Clean invoice matching sample_po exactly."""
    return Invoice(
        invoice_number="INV-TEST-001",
        supplier_id="SUP-001",
        supplier_name="Apex Paper Co.",
        po_number="PO-TEST-001",
        invoice_date=date(2024, 3, 15),
        due_date=date(2024, 4, 14),
        payment_terms="Net 30",
        line_items=[grade_a_line],
        total_amount=21000.0,
    )


@pytest.fixture
def sample_grn(grade_a_line) -> GoodsReceiptNote:
    """GRN matching the straight-through PO/invoice exactly."""
    return GoodsReceiptNote(
        gr_number="GR-TEST-001",
        po_number="PO-TEST-001",
        invoice_number="INV-TEST-001",
        supplier_id="SUP-001",
        date_received=date(2024, 3, 14),
        received_by="warehouse.smith@meridian.com",
        line_items=[grade_a_line],
    )


# ---------------------------------------------------------------------------
# Document fixtures — informal modification (Grade A → Grade B substitution)
# ---------------------------------------------------------------------------

@pytest.fixture
def informal_mod_po() -> PurchaseOrder:
    """PO for 500 units Standard grade @ $42 = $21,000."""
    return PurchaseOrder(
        po_number="PO-TEST-002",
        supplier_id="SUP-001",
        supplier_name="Apex Paper Co.",
        created_by="buyer.johnson@meridian.com",
        department="Office Supplies",
        cost_center="CC-100",
        creation_date=date(2024, 3, 1),
        line_items=[
            LineItem(
                sku="AP-CPA-STD",
                description="A4 Copy Paper Standard",
                product_grade="Standard",
                unit_price=42.0,
                quantity=500,
                total=21000.0,
            )
        ],
        total_amount=21000.0,
    )


@pytest.fixture
def informal_mod_invoice() -> Invoice:
    """Invoice: 450 Standard @ $42 + 50 Premium @ $58 = $21,800."""
    return Invoice(
        invoice_number="INV-TEST-002",
        supplier_id="SUP-001",
        supplier_name="Apex Paper Co.",
        po_number="PO-TEST-002",
        invoice_date=date(2024, 3, 15),
        due_date=date(2024, 4, 14),
        payment_terms="Net 30",
        line_items=[
            LineItem(
                sku="AP-CPA-STD",
                description="A4 Copy Paper Standard",
                product_grade="Standard",
                unit_price=42.0,
                quantity=450,
                total=18900.0,
            ),
            LineItem(
                sku="AP-CPA-PRM",
                description="A4 Copy Paper Premium",
                product_grade="Premium",
                unit_price=58.0,
                quantity=50,
                total=2900.0,
            ),
        ],
        total_amount=21800.0,
    )


@pytest.fixture
def informal_mod_grn() -> GoodsReceiptNote:
    """GRN matching the invoice quantities (not the PO)."""
    return GoodsReceiptNote(
        gr_number="GR-TEST-002",
        po_number="PO-TEST-002",
        invoice_number="INV-TEST-002",
        supplier_id="SUP-001",
        date_received=date(2024, 3, 14),
        received_by="warehouse.smith@meridian.com",
        line_items=[
            LineItem(
                sku="AP-CPA-STD",
                description="A4 Copy Paper Standard",
                product_grade="Standard",
                unit_price=42.0,
                quantity=450,
                total=18900.0,
            ),
            LineItem(
                sku="AP-CPA-PRM",
                description="A4 Copy Paper Premium",
                product_grade="Premium",
                unit_price=58.0,
                quantity=50,
                total=2900.0,
            ),
        ],
    )


@pytest.fixture
def informal_mod_triple(informal_mod_invoice, informal_mod_po, informal_mod_grn):
    """Convenience triple for the canonical informal modification scenario."""
    return informal_mod_invoice, informal_mod_po, informal_mod_grn


# ---------------------------------------------------------------------------
# Document fixtures — price variance (8% overcharge)
# ---------------------------------------------------------------------------

@pytest.fixture
def price_variance_po() -> PurchaseOrder:
    """PO: 100 units of MS-GLV-STD @ $15.50 = $1,550."""
    return PurchaseOrder(
        po_number="PO-TEST-003",
        supplier_id="SUP-002",
        supplier_name="MediSupply Corp.",
        created_by="buyer.chen@meridian.com",
        department="Medical Supplies",
        cost_center="CC-200",
        creation_date=date(2024, 2, 1),
        line_items=[
            LineItem(
                sku="MS-GLV-STD",
                description="Medical Gloves Standard",
                product_grade="Standard",
                unit_price=15.5,
                quantity=100,
                total=1550.0,
            )
        ],
        total_amount=1550.0,
    )


@pytest.fixture
def price_variance_invoice() -> Invoice:
    """Invoice: same 100 units but @ $16.74 (8% uplift) = $1,674."""
    return Invoice(
        invoice_number="INV-TEST-003",
        supplier_id="SUP-002",
        supplier_name="MediSupply Corp.",
        po_number="PO-TEST-003",
        invoice_date=date(2024, 2, 20),
        due_date=date(2024, 3, 21),
        payment_terms="Net 30",
        line_items=[
            LineItem(
                sku="MS-GLV-STD",
                description="Medical Gloves Standard",
                product_grade="Standard",
                unit_price=16.74,
                quantity=100,
                total=1674.0,
            )
        ],
        total_amount=1674.0,
    )


@pytest.fixture
def price_variance_triple(price_variance_invoice, price_variance_po):
    """Price variance triple — GRN is None (missing receipt)."""
    return price_variance_invoice, price_variance_po, None


# ---------------------------------------------------------------------------
# Exception record fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_exception_record() -> ExceptionRecord:
    """Pre-computed exception record for the informal modification scenario."""
    return ExceptionRecord(
        exception_id="EXC-TEST-001",
        po_number="PO-TEST-002",
        invoice_number="INV-TEST-002",
        supplier_id="SUP-001",
        exception_type=ExceptionType.INFORMAL_MODIFICATION,
        variance_amount=800.0,
        variance_percentage=3.81,
        description="Grade substitution: 50 Standard replaced by 50 Premium at higher price",
        related_email_ids=["EMAIL-TEST-001"],
        related_transcript_ids=[],
    )


# ---------------------------------------------------------------------------
# Communication fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_email() -> Email:
    """Email confirming a grade substitution."""
    return Email(
        email_id="EMAIL-TEST-001",
        subject="Re: PO-TEST-002 — Grade A Paper Shortage",
        sender="sales@apexpaper.com",
        receiver="buyer.johnson@meridian.com",
        date=date(2024, 3, 10),
        body=(
            "Hi, due to a temporary shortage of Standard grade paper we will be "
            "substituting 50 reams with Premium grade. The price difference is $16/ream. "
            "Please confirm this is acceptable."
        ),
        related_po="PO-TEST-002",
        related_invoice="INV-TEST-002",
    )


@pytest.fixture
def sample_transcript() -> PhoneTranscript:
    """Phone call transcript discussing a grade substitution."""
    return PhoneTranscript(
        transcript_id="TRANS-TEST-001",
        caller="sales@apexpaper.com",
        caller_organization="Apex Paper Co.",
        callee="buyer.johnson@meridian.com",
        callee_organization="Meridian Corp",
        date=date(2024, 3, 11),
        duration_minutes=8,
        transcript=(
            "Caller: We confirmed via email the substitution of 50 Premium reams. "
            "Callee: Yes, that was approved verbally. We'll update the PO retrospectively."
        ),
        related_po="PO-TEST-002",
        related_invoice="INV-TEST-002",
    )


# ---------------------------------------------------------------------------
# Supplier fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_supplier() -> Supplier:
    """Apex Paper Co. supplier record (no catalog for simplicity)."""
    return Supplier(
        supplier_id="SUP-001",
        name="Apex Paper Co.",
        contact_email="sales@apexpaper.com",
        contact_phone="+1-555-0101",
        address="123 Paper Mill Rd, Portland, OR 97201",
        payment_terms="Net 30",
        currency="USD",
        active=True,
    )


# ---------------------------------------------------------------------------
# InvoiceException fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_exception(sample_invoice, sample_po, sample_grn) -> InvoiceException:
    """An InvoiceException in RECEIVED state with no detected exception types."""
    return InvoiceException(
        invoice=sample_invoice,
        purchase_order=sample_po,
        grn=sample_grn,
    )


@pytest.fixture
def informal_mod_exception(
    informal_mod_invoice,
    informal_mod_po,
    informal_mod_grn,
    sample_exception_record,
    sample_email,
) -> InvoiceException:
    """An InvoiceException representing the canonical informal modification case."""
    return InvoiceException(
        invoice=informal_mod_invoice,
        purchase_order=informal_mod_po,
        grn=informal_mod_grn,
        exception_record=sample_exception_record,
        exception_types=[ExceptionType.INFORMAL_MODIFICATION],
        related_emails=[sample_email],
        total_variance_usd=800.0,
    )
