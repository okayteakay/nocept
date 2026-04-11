"""Shared pytest fixtures for the AP Exception Resolution Agent test suite."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import fakeredis
import pytest

from clients.redis_client import RedisStreamsClient
from clients.tavily_client import TavilyClient, TavilySearchResult
from config.settings import AppConfig
from models.exception import ExceptionState, ExceptionType, InvoiceException, LineItemVariance
from models.grn import GoodsReceiptNote, GRNLineItem
from models.invoice import Invoice, LineItem
from models.purchase_order import POLineItem, PurchaseOrder
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
        title="Acme Paper Co. Temporary Grade A Shortage",
        url="https://example.com/acme-shortage",
        content="Acme Paper Co. has announced a temporary shortage of Grade A paper. Grade B is being offered as a substitute at a 60% premium.",
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
        WATSONX_API_KEY="test-key",
        WATSONX_PROJECT_ID="test-project",
        PRICE_TOLERANCE_PCT=0.03,
        QTY_TOLERANCE_PCT=0.02,
        AUTO_RESOLVE_MAX_VARIANCE_USD=200.0,
    )


# ---------------------------------------------------------------------------
# Document fixtures — straight-through (no exception)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_line_item() -> LineItem:
    return LineItem(
        sku="PAPER-A-REAM",
        description="Grade A Office Paper, 500-sheet ream",
        quantity=Decimal("500"),
        unit_price=Decimal("50.00"),
        line_total=Decimal("25000.00"),
    )


@pytest.fixture
def sample_po(sample_line_item) -> PurchaseOrder:
    return PurchaseOrder(
        po_number="PO-TEST-001",
        supplier_id="SUPP-001",
        supplier_name="Acme Paper Co.",
        buyer_id="BUYER-007",
        created_date=date(2024, 3, 1),
        line_items=[
            POLineItem(
                sku=sample_line_item.sku,
                description=sample_line_item.description,
                quantity=sample_line_item.quantity,
                unit_price=sample_line_item.unit_price,
                line_total=sample_line_item.line_total,
            )
        ],
        total_amount=Decimal("25000.00"),
    )


@pytest.fixture
def sample_invoice(sample_line_item) -> Invoice:
    return Invoice(
        invoice_id="INV-TEST-001",
        supplier_id="SUPP-001",
        supplier_name="Acme Paper Co.",
        po_number="PO-TEST-001",
        invoice_date=date(2024, 3, 15),
        line_items=[sample_line_item],
        total_amount=Decimal("25000.00"),
    )


@pytest.fixture
def sample_grn() -> GoodsReceiptNote:
    return GoodsReceiptNote(
        grn_id="GRN-TEST-001",
        po_number="PO-TEST-001",
        supplier_id="SUPP-001",
        receipt_date=date(2024, 3, 14),
        line_items=[
            GRNLineItem(
                sku="PAPER-A-REAM",
                quantity_received=Decimal("500"),
                received_date=date(2024, 3, 14),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Document fixtures — informal modification (Grade A → Grade B substitution)
# ---------------------------------------------------------------------------

@pytest.fixture
def informal_mod_po() -> PurchaseOrder:
    """PO for 500 reams of Grade A @ $50 = $25,000."""
    return PurchaseOrder(
        po_number="PO-TEST-002",
        supplier_id="SUPP-001",
        supplier_name="Acme Paper Co.",
        buyer_id="BUYER-007",
        created_date=date(2024, 3, 1),
        line_items=[
            POLineItem(
                sku="PAPER-A-REAM",
                description="Grade A Office Paper",
                quantity=Decimal("500"),
                unit_price=Decimal("50.00"),
                line_total=Decimal("25000.00"),
            )
        ],
        total_amount=Decimal("25000.00"),
    )


@pytest.fixture
def informal_mod_invoice() -> Invoice:
    """Invoice: 450 Grade A @ $50 + 50 Grade B @ $80 = $26,500."""
    return Invoice(
        invoice_id="INV-TEST-002",
        supplier_id="SUPP-001",
        supplier_name="Acme Paper Co.",
        po_number="PO-TEST-002",
        invoice_date=date(2024, 3, 15),
        line_items=[
            LineItem(
                sku="PAPER-A-REAM",
                description="Grade A Office Paper",
                quantity=Decimal("450"),
                unit_price=Decimal("50.00"),
                line_total=Decimal("22500.00"),
            ),
            LineItem(
                sku="PAPER-B-REAM",
                description="Grade B Office Paper (substitution)",
                quantity=Decimal("50"),
                unit_price=Decimal("80.00"),
                line_total=Decimal("4000.00"),
            ),
        ],
        total_amount=Decimal("26500.00"),
    )


@pytest.fixture
def informal_mod_grn() -> GoodsReceiptNote:
    """GRN matching the invoice quantities (not the PO)."""
    return GoodsReceiptNote(
        grn_id="GRN-TEST-002",
        po_number="PO-TEST-002",
        supplier_id="SUPP-001",
        receipt_date=date(2024, 3, 14),
        line_items=[
            GRNLineItem(
                sku="PAPER-A-REAM",
                quantity_received=Decimal("450"),
                received_date=date(2024, 3, 14),
            ),
            GRNLineItem(
                sku="PAPER-B-REAM",
                quantity_received=Decimal("50"),
                received_date=date(2024, 3, 14),
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
    return PurchaseOrder(
        po_number="PO-TEST-003",
        supplier_id="SUPP-002",
        supplier_name="Office Depot",
        buyer_id="BUYER-003",
        created_date=date(2024, 2, 1),
        line_items=[
            POLineItem(
                sku="WIDGET-X",
                description="Industrial Widget X",
                quantity=Decimal("100"),
                unit_price=Decimal("100.00"),
                line_total=Decimal("10000.00"),
            )
        ],
        total_amount=Decimal("10000.00"),
    )


@pytest.fixture
def price_variance_invoice() -> Invoice:
    return Invoice(
        invoice_id="INV-TEST-003",
        supplier_id="SUPP-002",
        supplier_name="Office Depot",
        po_number="PO-TEST-003",
        invoice_date=date(2024, 2, 20),
        line_items=[
            LineItem(
                sku="WIDGET-X",
                description="Industrial Widget X",
                quantity=Decimal("100"),
                unit_price=Decimal("108.00"),
                line_total=Decimal("10800.00"),
            )
        ],
        total_amount=Decimal("10800.00"),
    )


@pytest.fixture
def price_variance_triple(price_variance_invoice, price_variance_po, sample_grn):
    """Price variance triple (GRN matches PO quantity)."""
    return price_variance_invoice, price_variance_po, None
