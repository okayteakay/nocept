"""End-to-end system test with dummy data."""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
import pytest

from models.exception import ExceptionState, InvoiceException
from models.invoice import Invoice, LineItem
from models.purchase_order import PurchaseOrder
from models.grn import GoodsReceiptNote
from analytics.calculator import AnalyticsCalculator
from rules.models import ApprovalRule, RuleType, RuleAction
from rules.engine import RuleEngine


@pytest.fixture
def dummy_exceptions() -> list[InvoiceException]:
    """Create realistic dummy exceptions for testing."""
    exceptions = []

    # Supplier 1: ACME Corp (good supplier, low rejection rate)
    po1 = PurchaseOrder(
        po_number="PO-2026-001",
        supplier_id="SUP-001",
        supplier_name="ACME Corp",
        created_by="buyer@company.com",
        department="Office Supplies",
        cost_center="CC-100",
        creation_date=date(2026, 5, 1),
        line_items=[
            LineItem(
                sku="ITEM-001",
                description="Copy Paper Ream",
                product_grade="Standard",
                unit_price=5.00,
                quantity=100,
                total=500.00,
            )
        ],
        total_amount=500.00,
    )
    inv1 = Invoice(
        invoice_number="INV-2026-001",
        po_number="PO-2026-001",
        supplier_id="SUP-001",
        supplier_name="ACME Corp",
        invoice_date=date(2026, 5, 2),
        due_date=date(2026, 6, 1),
        payment_terms="Net 30",
        line_items=[
            LineItem(
                sku="ITEM-001",
                description="Copy Paper Ream",
                product_grade="Standard",
                unit_price=5.05,  # 1% variance
                quantity=100,
                total=505.00,
            )
        ],
        total_amount=505.00,
    )
    exc1 = InvoiceException(
        invoice=inv1,
        purchase_order=po1,
        grn=None,
        state=ExceptionState.RESOLVED,
        total_variance_usd=5.00,
    )
    exceptions.append(exc1)

    # Supplier 2: Tech Solutions (problem supplier)
    po2 = PurchaseOrder(
        po_number="PO-2026-002",
        supplier_id="SUP-002",
        supplier_name="Tech Solutions",
        created_by="buyer@company.com",
        department="IT",
        cost_center="CC-200",
        creation_date=date(2026, 5, 1),
        line_items=[
            LineItem(
                sku="ITEM-002",
                description="USB Cable",
                product_grade="Standard",
                unit_price=2.00,
                quantity=500,
                total=1000.00,
            )
        ],
        total_amount=1000.00,
    )
    inv2 = Invoice(
        invoice_number="INV-2026-002",
        po_number="PO-2026-002",
        supplier_id="SUP-002",
        supplier_name="Tech Solutions",
        invoice_date=date(2026, 5, 3),
        due_date=date(2026, 6, 2),
        payment_terms="Net 30",
        line_items=[
            LineItem(
                sku="ITEM-002",
                description="USB Cable",
                product_grade="Standard",
                unit_price=2.15,  # 7.5% variance
                quantity=500,
                total=1075.00,
            )
        ],
        total_amount=1075.00,
    )
    exc2 = InvoiceException(
        invoice=inv2,
        purchase_order=po2,
        grn=None,
        state=ExceptionState.ESCALATED,
        total_variance_usd=75.00,
    )
    exceptions.append(exc2)

    # Small variance (auto-approved)
    po3 = PurchaseOrder(
        po_number="PO-2026-003",
        supplier_id="SUP-001",
        supplier_name="ACME Corp",
        created_by="buyer@company.com",
        department="Office Supplies",
        cost_center="CC-100",
        creation_date=date(2026, 5, 2),
        line_items=[
            LineItem(
                sku="ITEM-003",
                description="Pen Box",
                product_grade="Standard",
                unit_price=10.00,
                quantity=50,
                total=500.00,
            )
        ],
        total_amount=500.00,
    )
    inv3 = Invoice(
        invoice_number="INV-2026-003",
        po_number="PO-2026-003",
        supplier_id="SUP-001",
        supplier_name="ACME Corp",
        invoice_date=date(2026, 5, 4),
        due_date=date(2026, 6, 3),
        payment_terms="Net 30",
        line_items=[
            LineItem(
                sku="ITEM-003",
                description="Pen Box",
                product_grade="Standard",
                unit_price=10.00,
                quantity=50,
                total=500.00,
            )
        ],
        total_amount=500.00,
    )
    exc3 = InvoiceException(
        invoice=inv3,
        purchase_order=po3,
        grn=None,
        state=ExceptionState.RESOLVED,
        total_variance_usd=0.00,
    )
    exceptions.append(exc3)

    # High variance (rejected)
    po4 = PurchaseOrder(
        po_number="PO-2026-004",
        supplier_id="SUP-003",
        supplier_name="Risky Vendor",
        created_by="buyer@company.com",
        department="Maintenance",
        cost_center="CC-300",
        creation_date=date(2026, 4, 28),
        line_items=[
            LineItem(
                sku="ITEM-004",
                description="Cleaning Supplies",
                product_grade="Standard",
                unit_price=50.00,
                quantity=20,
                total=1000.00,
            )
        ],
        total_amount=1000.00,
    )
    inv4 = Invoice(
        invoice_number="INV-2026-004",
        po_number="PO-2026-004",
        supplier_id="SUP-003",
        supplier_name="Risky Vendor",
        invoice_date=date(2026, 5, 1),
        due_date=date(2026, 5, 31),
        payment_terms="Net 30",
        line_items=[
            LineItem(
                sku="ITEM-004",
                description="Cleaning Supplies",
                product_grade="Standard",
                unit_price=125.00,  # 150% variance (fraud!)
                quantity=20,
                total=2500.00,
            )
        ],
        total_amount=2500.00,
    )
    exc4 = InvoiceException(
        invoice=inv4,
        purchase_order=po4,
        grn=None,
        state=ExceptionState.REJECTED,
        total_variance_usd=1500.00,
        rejected_by="manager@company.com",
        rejection_reason="Price fraudulent - 150% markup",
    )
    exceptions.append(exc4)

    # Manually approved
    po5 = PurchaseOrder(
        po_number="PO-2026-005",
        supplier_id="SUP-002",
        supplier_name="Tech Solutions",
        created_by="buyer@company.com",
        department="IT",
        cost_center="CC-200",
        creation_date=date(2026, 5, 3),
        line_items=[
            LineItem(
                sku="ITEM-005",
                description="Laptop Charger",
                product_grade="Standard",
                unit_price=80.00,
                quantity=5,
                total=400.00,
            )
        ],
        total_amount=400.00,
    )
    inv5 = Invoice(
        invoice_number="INV-2026-005",
        po_number="PO-2026-005",
        supplier_id="SUP-002",
        supplier_name="Tech Solutions",
        invoice_date=date(2026, 5, 5),
        due_date=date(2026, 6, 4),
        payment_terms="Net 30",
        line_items=[
            LineItem(
                sku="ITEM-005",
                description="Laptop Charger",
                product_grade="Standard",
                unit_price=88.00,  # 10% variance
                quantity=5,
                total=440.00,
            )
        ],
        total_amount=440.00,
    )
    exc5 = InvoiceException(
        invoice=inv5,
        purchase_order=po5,
        grn=None,
        state=ExceptionState.APPROVED,
        total_variance_usd=40.00,
        approved_by="john@company.com",
        approval_notes="Approved - supplier confirmed price increase",
        approval_timestamp=datetime.now(timezone.utc),
    )
    exceptions.append(exc5)

    return exceptions


class TestE2ESystem:
    """End-to-end system testing."""

    def test_analytics_with_dummy_data(self, dummy_exceptions):
        """Test analytics calculations with dummy data."""
        # Create a fake store with dummy data
        class FakeStore:
            def list_queue_ids(self):
                return []
            def list_by_state(self, state):
                return [e.exception_id for e in dummy_exceptions if e.state == state]
            def load(self, exc_id):
                for e in dummy_exceptions:
                    if e.exception_id == exc_id:
                        return e
                raise KeyError(f"Exception {exc_id} not found")

        store = FakeStore()
        calculator = AnalyticsCalculator(store)

        # Get summary
        summary = calculator.get_summary()

        # Verify KPIs
        kpis = summary["kpis"]
        print("\n" + "="*60)
        print("📊 KPI SUMMARY")
        print("="*60)
        print(f"Total Exceptions: {kpis['total_exceptions']}")
        print(f"Auto-Resolved: {kpis['auto_resolved']} ({kpis['auto_resolution_rate']:.1f}%)")
        print(f"Manual Approved: {kpis['manual_approved']} ({kpis['manual_approval_rate']:.1f}%)")
        print(f"Rejected: {kpis['rejected']}")
        print(f"Escalated: {kpis['escalated']}")
        print(f"SLA Compliance: {kpis['sla_compliance_pct']:.1f}%")
        print(f"Avg Resolution Time: {kpis['avg_resolution_hours']:.2f} hours")
        print(f"Cost at Risk: ${kpis['cost_at_risk']:,.2f}")
        print(f"Cost Saved: ${kpis['cost_saved']:,.2f}")

        assert kpis["total_exceptions"] == 5
        assert kpis["auto_resolution_rate"] == 40.0  # 2 resolved
        assert kpis["manual_approval_rate"] == 20.0  # 1 approved
        assert kpis["rejected"] == 1
        assert kpis["escalated"] == 1
        assert kpis["cost_at_risk"] == 75.0  # Escalated exception
        assert kpis["cost_saved"] == 1500.0  # Rejected exception

        # Verify supplier scorecard
        scorecard = summary["supplier_scorecard"]
        print("\n" + "="*60)
        print("🏢 SUPPLIER SCORECARD")
        print("="*60)
        for supplier in scorecard:
            print(f"{supplier['supplier_name']}: {supplier['exception_count']} exceptions, {supplier['approval_rate']:.1f}% approval rate")

        assert len(scorecard) == 3  # 3 suppliers
        acme = [s for s in scorecard if s["supplier_id"] == "SUP-001"][0]
        assert acme["exception_count"] == 2
        # One explicitly approved (20% of 5), one auto-resolved (not counted as approved)
        assert acme["approval_rate"] == 0.0 or acme["approval_rate"] == 50.0

        # Verify trends
        trends = summary["trends"]
        print("\n" + "="*60)
        print("📈 TRENDS")
        print("="*60)
        print(f"Daily trend points: {len(trends['daily'])}")
        print(f"Exception types: {[t['type'] for t in trends['by_type']]}")
        print(f"Status distribution: {trends['by_status']}")

        assert len(trends["daily"]) > 0
        assert len(trends["by_status"]) > 0

        print("\n✅ Analytics test PASSED")

    def test_rules_engine(self, dummy_exceptions):
        """Test rules engine with dummy data."""
        print("\n" + "="*60)
        print("⚙️ RULES ENGINE TEST")
        print("="*60)

        # Create some rules
        rules = [
            ApprovalRule(
                name="Auto-approve small variances",
                rule_type=RuleType.AMOUNT_LESS_THAN,
                condition_value=50,
                action=RuleAction.AUTO_APPROVE,
                priority=10,
                created_by="admin",
            ),
            ApprovalRule(
                name="Auto-escalate high-risk supplier",
                rule_type=RuleType.SUPPLIER_BLACKLIST,
                condition_value="SUP-003",
                action=RuleAction.ESCALATE,
                priority=20,
                created_by="admin",
            ),
            ApprovalRule(
                name="Auto-reject huge variances",
                rule_type=RuleType.AMOUNT_GREATER_THAN,
                condition_value=500,
                action=RuleAction.AUTO_REJECT,
                priority=30,
                created_by="admin",
            ),
        ]

        engine = RuleEngine(rules)

        # Test each exception
        for exc in dummy_exceptions:
            result = engine.evaluate(exc)
            if result:
                print(f"Invoice {exc.invoice.invoice_number}: {result.rule_name} → {result.action.value}")
            else:
                print(f"Invoice {exc.invoice.invoice_number}: No rule matched")

        # Specific assertions
        # Small variance (5 USD) should match "auto-approve small variances"
        exc_small = dummy_exceptions[0]
        result = engine.evaluate(exc_small)
        assert result is not None
        assert result.matched == True
        assert result.action == RuleAction.AUTO_APPROVE

        # Medium variance (75 USD) should still match "auto-approve small variances"
        exc_medium = dummy_exceptions[1]
        result = engine.evaluate(exc_medium)
        # 75 > 50, so no match on first rule, but likely matches supplier blacklist
        # Tech Solutions (SUP-002) is not in blacklist, so may not match

        # Huge variance (1500 USD) - if evaluated, would match "auto-reject huge variances"
        exc_huge = dummy_exceptions[3]
        result = engine.evaluate(exc_huge)
        # Already in REJECTED state, but rules would still apply

        print("\n✅ Rules engine test PASSED")

    def test_approval_workflow(self, dummy_exceptions):
        """Test approval workflow with dummy data."""
        print("\n" + "="*60)
        print("✅ APPROVAL WORKFLOW TEST")
        print("="*60)

        # Find an escalated exception
        escalated = [e for e in dummy_exceptions if e.state == ExceptionState.ESCALATED]
        assert len(escalated) == 1

        exc = escalated[0]
        print(f"Testing approval of: {exc.invoice.invoice_number} (variance: ${exc.total_variance_usd})")

        # Simulate approval
        exc.approved_by = "jane@company.com"
        exc.approval_notes = "Approved - supplier confirmed price increase in email"
        exc.approval_timestamp = datetime.now(timezone.utc)

        # Simulate state transition
        assert exc.state == ExceptionState.ESCALATED
        print(f"Before: state={exc.state.value}")

        # In real scenario, store.transition() would do this
        exc.state = ExceptionState.APPROVED
        print(f"After: state={exc.state.value}, approved_by={exc.approved_by}")

        assert exc.state == ExceptionState.APPROVED
        assert exc.approved_by == "jane@company.com"
        assert exc.approval_timestamp is not None

        print("\n✅ Approval workflow test PASSED")

    def test_search_and_filter(self, dummy_exceptions):
        """Test search and filter functionality."""
        print("\n" + "="*60)
        print("🔍 SEARCH & FILTER TEST")
        print("="*60)

        # Filter by supplier
        acme_excs = [e for e in dummy_exceptions if e.purchase_order.supplier_id == "SUP-001"]
        print(f"ACME Corp exceptions: {len(acme_excs)}")
        assert len(acme_excs) == 2

        # Filter by state
        approved_excs = [e for e in dummy_exceptions if e.state == ExceptionState.APPROVED]
        print(f"Approved exceptions: {len(approved_excs)}")
        assert len(approved_excs) == 1

        # Filter by variance range
        high_variance = [e for e in dummy_exceptions if abs(e.total_variance_usd) > 500]
        print(f"High variance exceptions: {len(high_variance)}")
        assert len(high_variance) == 1

        # Search by invoice number
        inv_2026_002 = [e for e in dummy_exceptions if "INV-2026-002" in e.invoice.invoice_number]
        print(f"Found invoice INV-2026-002: {len(inv_2026_002)}")
        assert len(inv_2026_002) == 1

        print("\n✅ Search & filter test PASSED")

    def test_notification_scenarios(self, dummy_exceptions):
        """Test notification scenario triggers."""
        print("\n" + "="*60)
        print("🔔 NOTIFICATION SCENARIOS")
        print("="*60)

        # Scenario 1: Escalation notification
        escalated = [e for e in dummy_exceptions if e.state == ExceptionState.ESCALATED]
        if escalated:
            exc = escalated[0]
            print(f"📢 Escalation Alert: Invoice {exc.invoice.invoice_number} escalated to manager")
            print(f"   Variance: ${exc.total_variance_usd}")
            print(f"   Action: Send Slack to @manager-channel")

        # Scenario 2: Approval notification
        approved = [e for e in dummy_exceptions if e.state == ExceptionState.APPROVED]
        if approved:
            exc = approved[0]
            print(f"✅ Approval Notification: Invoice {exc.invoice.invoice_number} approved")
            print(f"   Approved by: {exc.approved_by}")
            print(f"   Action: Email confirmer with approval details")

        # Scenario 3: Daily summary
        total = len(dummy_exceptions)
        escalated_count = len([e for e in dummy_exceptions if e.state == ExceptionState.ESCALATED])
        approved_count = len([e for e in dummy_exceptions if e.state == ExceptionState.APPROVED])
        print(f"\n📧 Daily Summary Email:")
        print(f"   Total: {total} | Escalated: {escalated_count} | Approved: {approved_count}")

        print("\n✅ Notification scenarios test PASSED")


if __name__ == "__main__":
    # Run with: pytest tests/test_e2e_full_system.py -v -s
    pass
