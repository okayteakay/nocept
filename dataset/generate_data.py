"""
Synthetic data generator for Invoice Exception Resolution Agent.
Generates 200 PO-Invoice pairs, 100 emails, and 40 phone transcripts.
"""

import json
import random
import os
from datetime import date, timedelta
from models import (
    PurchaseOrder, Invoice, GoodsReceipt, LineItem, Email,
    PhoneTranscript, ExceptionRecord, ExceptionType, Supplier,
)

random.seed(42)

# ---------------------------------------------------------------------------
# Load catalog
# ---------------------------------------------------------------------------
with open(os.path.join(os.path.dirname(__file__), "catalog.json")) as f:
    CATALOG = json.load(f)

SUPPLIERS = CATALOG["suppliers"]
BUYERS = CATALOG["buyers"]
RECEIVERS = CATALOG["warehouse_receivers"]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TOTAL_RECORDS = 200
NUM_CLEAN = 130
NUM_EXCEPTIONS = 70

EXCEPTION_COUNTS = {
    ExceptionType.PRICE_VARIANCE: 15,
    ExceptionType.QUANTITY_VARIANCE: 14,
    ExceptionType.MISSING_GOODS_RECEIPT: 10,
    ExceptionType.DUPLICATE_INVOICE: 8,
    ExceptionType.INFORMAL_MODIFICATION: 23,
}

TOTAL_EMAILS = 100
TOTAL_TRANSCRIPTS = 40
EXCEPTION_EMAILS = 30
EXCEPTION_TRANSCRIPTS = 18

PAYMENT_TERMS = ["Net 30", "Net 45", "Net 60", "2/10 Net 30"]
BASE_DATE = date(2026, 1, 5)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rand_date(start: date, days_range: int) -> date:
    return start + timedelta(days=random.randint(0, days_range))


def pick_supplier():
    return random.choice(SUPPLIERS)


def pick_product(supplier):
    product_cat = random.choice(supplier["products"])
    grade = product_cat["grades"][0]  # default: lowest grade
    return product_cat, grade


def pick_upgrade(product_cat, current_grade_idx=0):
    """Return a higher grade from the same product category."""
    grades = product_cat["grades"]
    if current_grade_idx + 1 < len(grades):
        return grades[current_grade_idx + 1]
    return grades[-1]


def pick_buyer():
    return random.choice(BUYERS)


def make_line_item(grade, qty) -> LineItem:
    return LineItem(
        sku=grade["sku"],
        description=grade["description"],
        product_grade=grade["grade"],
        unit_price=grade["unit_price"],
        quantity=qty,
        total=round(grade["unit_price"] * qty, 2),
    )


# ---------------------------------------------------------------------------
# Record assignment — decide which indices are which exception type
# ---------------------------------------------------------------------------
indices = list(range(TOTAL_RECORDS))
random.shuffle(indices)

exception_assignments: dict[int, ExceptionType] = {}
cursor = 0
for exc_type, count in EXCEPTION_COUNTS.items():
    for i in range(count):
        exception_assignments[indices[cursor]] = exc_type
        cursor += 1

# Remaining indices are clean
clean_indices = set(indices[cursor:])

# For duplicates, pair them up (original idx -> duplicate idx)
dup_exception_indices = [
    idx for idx, t in exception_assignments.items()
    if t == ExceptionType.DUPLICATE_INVOICE
]

# ---------------------------------------------------------------------------
# Assign communications to exceptions
# ---------------------------------------------------------------------------
# Informal modifications get most communications
informal_indices = [
    idx for idx, t in exception_assignments.items()
    if t == ExceptionType.INFORMAL_MODIFICATION
]
price_var_indices = [
    idx for idx, t in exception_assignments.items()
    if t == ExceptionType.PRICE_VARIANCE
]
qty_var_indices = [
    idx for idx, t in exception_assignments.items()
    if t == ExceptionType.QUANTITY_VARIANCE
]

# Email assignments: 30 exception-linked
email_exception_pool = (
    random.sample(informal_indices, min(15, len(informal_indices)))
    + random.sample(price_var_indices, min(8, len(price_var_indices)))
    + random.sample(qty_var_indices, min(5, len(qty_var_indices)))
    + random.sample(
        [i for i, t in exception_assignments.items()
         if t == ExceptionType.MISSING_GOODS_RECEIPT],
        min(2, 10),
    )
)
email_exception_pool = email_exception_pool[:EXCEPTION_EMAILS]

# Transcript assignments: 18 exception-linked
transcript_exception_pool = (
    random.sample(informal_indices, min(12, len(informal_indices)))
    + random.sample(price_var_indices, min(3, len(price_var_indices)))
    + random.sample(qty_var_indices, min(3, len(qty_var_indices)))
)
transcript_exception_pool = transcript_exception_pool[:EXCEPTION_TRANSCRIPTS]

# ---------------------------------------------------------------------------
# Generate POs, Invoices, Goods Receipts
# ---------------------------------------------------------------------------
purchase_orders: list[dict] = []
invoices: list[dict] = []
goods_receipts: list[dict] = []
exception_records: list[dict] = []
supplier_list: list[dict] = []

# Track generated data for cross-referencing with comms
record_details: dict[int, dict] = {}

# Build supplier list for output
for s in SUPPLIERS:
    supplier_list.append(Supplier(
        supplier_id=s["supplier_id"],
        name=s["name"],
        contact_person=s["contact_person"],
        contact_email=s["contact_email"],
        phone=s["phone"],
        category=s["category"],
    ).model_dump(mode="json"))

for idx in range(TOTAL_RECORDS):
    po_num = f"PO-{idx + 1:04d}"
    inv_num = f"INV-{idx + 1:04d}"
    gr_num = f"GR-{idx + 1:04d}"
    exc_type = exception_assignments.get(idx, ExceptionType.NONE)

    supplier = pick_supplier()
    product_cat, base_grade = pick_product(supplier)
    buyer = pick_buyer()
    base_qty = random.choice([50, 100, 150, 200, 250, 300, 400, 500, 750, 1000])
    po_date = rand_date(BASE_DATE, 65)  # Jan 5 – Mar 11
    inv_date = po_date + timedelta(days=random.randint(7, 25))
    due_date = inv_date + timedelta(days=30)
    terms = random.choice(PAYMENT_TERMS)

    # --- PO (always reflects what was originally ordered) ---
    po_item = make_line_item(base_grade, base_qty)
    po = PurchaseOrder(
        po_number=po_num,
        supplier_id=supplier["supplier_id"],
        supplier_name=supplier["name"],
        line_items=[po_item],
        total_amount=po_item.total,
        creation_date=po_date,
        created_by=buyer["name"],
        department=buyer["department"],
        cost_center=buyer["cost_center"],
    )

    # --- Invoice (may differ based on exception type) ---
    inv_items = [po_item.model_copy()]
    variance_amt = 0.0
    variance_pct = 0.0
    exc_description = ""
    gr_notes = None
    skip_gr = False

    if exc_type == ExceptionType.NONE:
        # Clean match
        exc_description = ""

    elif exc_type == ExceptionType.PRICE_VARIANCE:
        pct_change = random.choice([0.03, 0.05, 0.08, 0.10, 0.15, 0.02, 0.12])
        direction = random.choice([1, 1, 1, -1])  # mostly over-invoiced
        new_price = round(base_grade["unit_price"] * (1 + direction * pct_change), 2)
        inv_items = [LineItem(
            sku=base_grade["sku"],
            description=base_grade["description"],
            product_grade=base_grade["grade"],
            unit_price=new_price,
            quantity=base_qty,
            total=round(new_price * base_qty, 2),
        )]
        variance_amt = round(inv_items[0].total - po_item.total, 2)
        variance_pct = round((variance_amt / po_item.total) * 100, 2)
        exc_description = (
            f"Unit price mismatch: PO has ${base_grade['unit_price']:.2f}, "
            f"invoice has ${new_price:.2f} ({'+' if variance_amt > 0 else ''}"
            f"{variance_pct}%)"
        )

    elif exc_type == ExceptionType.QUANTITY_VARIANCE:
        qty_diff = random.choice([-50, -30, -20, -10, 10, 20, 25])
        if abs(qty_diff) >= base_qty:
            qty_diff = int(base_qty * random.choice([-0.1, -0.2, 0.1]))
        inv_qty = base_qty + qty_diff
        if inv_qty <= 0:
            inv_qty = max(int(base_qty * 0.8), 1)
            qty_diff = inv_qty - base_qty
        inv_items = [make_line_item(base_grade, inv_qty)]
        variance_amt = round(inv_items[0].total - po_item.total, 2)
        variance_pct = round((variance_amt / po_item.total) * 100, 2)
        exc_description = (
            f"Quantity mismatch: PO has {base_qty} units, "
            f"invoice has {inv_qty} units (diff: {qty_diff})"
        )
        gr_notes = f"Received {inv_qty} units instead of {base_qty} ordered."

    elif exc_type == ExceptionType.MISSING_GOODS_RECEIPT:
        skip_gr = True
        exc_description = (
            f"No goods receipt recorded for {po_num}. "
            f"Invoice received from {supplier['name']} for ${po_item.total:,.2f}."
        )

    elif exc_type == ExceptionType.DUPLICATE_INVOICE:
        # Invoice is identical content but different invoice number
        inv_num = f"INV-{idx + 1:04d}-DUP"
        exc_description = (
            f"Duplicate invoice detected. Invoice {inv_num} matches content of "
            f"a prior invoice for {po_num} from {supplier['name']}."
        )

    elif exc_type == ExceptionType.INFORMAL_MODIFICATION:
        upgrade_grade = pick_upgrade(product_cat, 0)
        # Several sub-scenarios
        scenario = random.choice([
            "partial_substitution",
            "full_substitution",
            "expedited_shipping",
            "partial_substitution",
            "partial_substitution",
        ])

        if scenario == "partial_substitution":
            kept_qty = int(base_qty * random.choice([0.7, 0.75, 0.8, 0.85, 0.9]))
            sub_qty = base_qty - kept_qty
            item_original = make_line_item(base_grade, kept_qty)
            item_sub = make_line_item(upgrade_grade, sub_qty)
            inv_items = [item_original, item_sub]
            inv_total = round(item_original.total + item_sub.total, 2)
            variance_amt = round(inv_total - po_item.total, 2)
            variance_pct = round((variance_amt / po_item.total) * 100, 2)
            exc_description = (
                f"Partial substitution: PO requested {base_qty}x "
                f"{base_grade['description']} (${base_grade['unit_price']:.2f}/unit). "
                f"Invoice shows {kept_qty}x {base_grade['grade']} + "
                f"{sub_qty}x {upgrade_grade['grade']} "
                f"(${upgrade_grade['unit_price']:.2f}/unit). "
                f"Total changed from ${po_item.total:,.2f} to ${inv_total:,.2f}."
            )
            gr_notes = (
                f"Received mixed shipment: {kept_qty}x {base_grade['grade']}, "
                f"{sub_qty}x {upgrade_grade['grade']}. No change order on file."
            )

        elif scenario == "full_substitution":
            item_sub = make_line_item(upgrade_grade, base_qty)
            inv_items = [item_sub]
            variance_amt = round(item_sub.total - po_item.total, 2)
            variance_pct = round((variance_amt / po_item.total) * 100, 2)
            exc_description = (
                f"Full substitution: PO requested {base_qty}x "
                f"{base_grade['description']}. Invoice shows {base_qty}x "
                f"{upgrade_grade['description']} at "
                f"${upgrade_grade['unit_price']:.2f}/unit. "
                f"Total changed from ${po_item.total:,.2f} to ${item_sub.total:,.2f}."
            )
            gr_notes = (
                f"Received {base_qty}x {upgrade_grade['grade']} instead of "
                f"{base_grade['grade']}. Product substitution — no change order."
            )

        elif scenario == "expedited_shipping":
            shipping_surcharge = round(po_item.total * random.choice([0.05, 0.08, 0.10, 0.12]), 2)
            inv_items = [
                po_item.model_copy(),
                LineItem(
                    sku="SHIP-EXP",
                    description="Expedited Shipping Surcharge",
                    product_grade="N/A",
                    unit_price=shipping_surcharge,
                    quantity=1,
                    total=shipping_surcharge,
                ),
            ]
            variance_amt = shipping_surcharge
            variance_pct = round((variance_amt / po_item.total) * 100, 2)
            exc_description = (
                f"Expedited shipping surcharge of ${shipping_surcharge:,.2f} "
                f"added to invoice (not on PO). Buyer may have requested rush "
                f"delivery offline."
            )
            gr_notes = "Received via expedited carrier. Delivery was ahead of schedule."

    # Build final invoice
    inv_total = round(sum(it.total for it in inv_items), 2)
    invoice = Invoice(
        invoice_number=inv_num,
        po_number=po_num,
        supplier_id=supplier["supplier_id"],
        supplier_name=supplier["name"],
        line_items=inv_items,
        total_amount=inv_total,
        invoice_date=inv_date,
        due_date=due_date,
        payment_terms=terms,
    )

    # Build goods receipt (unless skipped)
    gr = None
    if not skip_gr:
        gr_items = inv_items  # GR reflects what actually arrived
        gr = GoodsReceipt(
            gr_number=gr_num,
            po_number=po_num,
            invoice_number=inv_num,
            supplier_id=supplier["supplier_id"],
            line_items=gr_items,
            date_received=inv_date - timedelta(days=random.randint(1, 5)),
            received_by=random.choice(RECEIVERS),
            notes=gr_notes,
        )

    # Store for cross-referencing
    record_details[idx] = {
        "po_num": po_num,
        "inv_num": inv_num,
        "supplier": supplier,
        "product_cat": product_cat,
        "base_grade": base_grade,
        "base_qty": base_qty,
        "po_total": po_item.total,
        "inv_total": inv_total,
        "buyer": buyer,
        "exc_type": exc_type,
        "variance_amt": variance_amt,
        "po_date": po_date,
        "inv_date": inv_date,
        "exc_description": exc_description,
    }
    # Also store upgrade info for informal mods
    if exc_type == ExceptionType.INFORMAL_MODIFICATION:
        upgrade = pick_upgrade(product_cat, 0)
        record_details[idx]["upgrade_grade"] = upgrade
        record_details[idx]["scenario"] = scenario if 'scenario' in dir() else "partial_substitution"

    purchase_orders.append(po.model_dump(mode="json"))
    invoices.append(invoice.model_dump(mode="json"))
    if gr:
        goods_receipts.append(gr.model_dump(mode="json"))

    if exc_type != ExceptionType.NONE:
        exc_rec = ExceptionRecord(
            exception_id=f"EXC-{idx + 1:04d}",
            po_number=po_num,
            invoice_number=inv_num,
            supplier_id=supplier["supplier_id"],
            exception_type=exc_type,
            variance_amount=variance_amt,
            variance_percentage=variance_pct,
            description=exc_description,
        )
        exception_records.append(exc_rec.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Email generation
# ---------------------------------------------------------------------------

def _supplier_first(name: str) -> str:
    return name.split()[0]


def _buyer_first(name: str) -> str:
    return name.split()[0]


INFORMAL_EMAIL_TEMPLATES = [
    lambda d: {
        "subject": f"Re: {d['po_num']} - Stock Availability Update",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"Following up on your order {d['po_num']}. We're currently running "
            f"low on {d['base_grade']['description']} — only have about "
            f"{int(d['base_qty'] * 0.8)} units in stock right now.\n\n"
            f"I can fill the remaining {d['base_qty'] - int(d['base_qty'] * 0.8)} "
            f"units with our {d['upgrade_grade']['description']} at "
            f"${d['upgrade_grade']['unit_price']:.2f}/unit instead of "
            f"${d['base_grade']['unit_price']:.2f}. Same specs, just higher grade.\n\n"
            f"Let me know if that works for you.\n\n"
            f"Best,\n{d['supplier']['contact_person']}\n{d['supplier']['name']}"
        ),
    },
    lambda d: {
        "subject": f"{d['po_num']} — Quick Update on Your Order",
        "body": (
            f"Hey {_buyer_first(d['buyer']['name'])},\n\n"
            f"Quick heads up — our warehouse flagged that we're short on "
            f"{d['base_grade']['grade']} {d['product_cat']['product_category']}. "
            f"I can still fill your full order of {d['base_qty']} units but would "
            f"need to swap some to {d['upgrade_grade']['grade']}. Price difference "
            f"is ${d['upgrade_grade']['unit_price'] - d['base_grade']['unit_price']:.2f}"
            f"/unit on the upgraded portion.\n\n"
            f"Want me to go ahead?\n\n"
            f"{d['supplier']['contact_person']}"
        ),
    },
    lambda d: {
        "subject": f"Order {d['po_num']} - Product Substitution Notice",
        "body": (
            f"Dear {d['buyer']['name']},\n\n"
            f"This is regarding your purchase order {d['po_num']} for "
            f"{d['base_qty']} units of {d['base_grade']['description']}.\n\n"
            f"Due to a temporary supply constraint, we are unable to fulfill the "
            f"complete order with {d['base_grade']['grade']} grade material. We "
            f"would like to propose substituting a portion with "
            f"{d['upgrade_grade']['grade']} grade, which meets or exceeds all "
            f"specifications of the original order.\n\n"
            f"The {d['upgrade_grade']['grade']} grade is priced at "
            f"${d['upgrade_grade']['unit_price']:.2f}/unit. Your revised order "
            f"total would be approximately ${d['inv_total']:,.2f}.\n\n"
            f"Please confirm at your earliest convenience so we can process "
            f"shipment.\n\n"
            f"Regards,\n{d['supplier']['contact_person']}\n{d['supplier']['name']}"
        ),
    },
    lambda d: {
        "subject": f"RE: {d['po_num']} - Go ahead with the swap",
        "body": (
            f"Hi {_supplier_first(d['supplier']['contact_person'])},\n\n"
            f"That works. Go ahead and ship the {d['upgrade_grade']['grade']} for "
            f"the portion you're short on. We need the full quantity by end of "
            f"month so let's not delay.\n\n"
            f"Thanks,\n{d['buyer']['name']}\nMeridian Corp"
        ),
        "reverse": True,  # buyer -> supplier
    },
    lambda d: {
        "subject": f"{d['po_num']} - Shipping upgrade per our conversation",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"As discussed, I'm processing {d['po_num']} with expedited shipping. "
            f"The rush delivery surcharge will be added to the invoice. "
            f"Estimate is around {random.choice([5, 8, 10, 12])}% of order value.\n\n"
            f"Shipment goes out today.\n\n"
            f"Best,\n{d['supplier']['contact_person']}"
        ),
    },
]

PRICE_VAR_EMAIL_TEMPLATES = [
    lambda d: {
        "subject": f"Price Adjustment Notice — Effective {d['po_date'].strftime('%B %Y')}",
        "body": (
            f"Dear Valued Customer,\n\n"
            f"Please be advised that effective "
            f"{d['po_date'].replace(day=1).strftime('%B 1, %Y')}, "
            f"pricing for {d['product_cat']['product_category']} has been adjusted "
            f"due to increased raw material costs.\n\n"
            f"Your current contract rate of "
            f"${d['base_grade']['unit_price']:.2f}/unit for "
            f"{d['base_grade']['description']} has been updated. "
            f"The new rate will be reflected on your next invoice.\n\n"
            f"We apologize for any inconvenience and appreciate your continued "
            f"partnership.\n\n"
            f"Sincerely,\n{d['supplier']['contact_person']}\n"
            f"Account Manager, {d['supplier']['name']}"
        ),
    },
    lambda d: {
        "subject": f"Re: {d['po_num']} — Updated Pricing",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"Just a heads up, the price on "
            f"{d['base_grade']['description']} went up this quarter. "
            f"Our new list price is a few percent above what's on your PO. "
            f"I tried to hold the old rate but corporate wouldn't budge.\n\n"
            f"Your invoice for {d['po_num']} will reflect the updated pricing. "
            f"Happy to discuss if you want to renegotiate for next quarter.\n\n"
            f"Cheers,\n{_supplier_first(d['supplier']['contact_person'])}"
        ),
    },
]

QTY_VAR_EMAIL_TEMPLATES = [
    lambda d: {
        "subject": f"{d['po_num']} — Partial Shipment Notice",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"We shipped your order {d['po_num']} today but could only fulfill "
            f"a partial quantity due to warehouse stock levels. "
            f"The remaining units are on backorder and should ship within "
            f"7-10 business days.\n\n"
            f"Your invoice reflects the quantity actually shipped.\n\n"
            f"Apologies for the inconvenience.\n\n"
            f"{d['supplier']['contact_person']}\n{d['supplier']['name']}"
        ),
    },
    lambda d: {
        "subject": f"Re: Where's the rest of {d['po_num']}?",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"Sorry about the short shipment on {d['po_num']}. We had an "
            f"inventory discrepancy at our distribution center. "
            f"I've invoiced only for what was actually delivered. "
            f"We'll send a separate invoice when the backorder ships.\n\n"
            f"Thanks for your patience,\n"
            f"{_supplier_first(d['supplier']['contact_person'])}"
        ),
    },
]

MISSING_GR_EMAIL_TEMPLATES = [
    lambda d: {
        "subject": f"Delivery Confirmation Request — {d['po_num']}",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"Our records show that {d['po_num']} was delivered on "
            f"{d['inv_date'].strftime('%B %d')} but we haven't received payment "
            f"acknowledgment yet. Could you confirm receipt on your end?\n\n"
            f"Thanks,\n{d['supplier']['contact_person']}\n{d['supplier']['name']}"
        ),
    },
]

ROUTINE_EMAIL_TEMPLATES = [
    lambda d: {
        "subject": f"Order Confirmation — {d['po_num']}",
        "body": (
            f"Dear {d['buyer']['name']},\n\n"
            f"This confirms receipt of your purchase order {d['po_num']} for "
            f"{d['base_qty']} units of {d['base_grade']['description']}.\n\n"
            f"Estimated delivery: "
            f"{(d['po_date'] + timedelta(days=random.randint(10, 20))).strftime('%B %d, %Y')}.\n\n"
            f"Thank you for your business.\n\n"
            f"Order Processing Team\n{d['supplier']['name']}"
        ),
    },
    lambda d: {
        "subject": f"Shipment Notification — {d['po_num']}",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"Your order {d['po_num']} has shipped. Tracking number: "
            f"{random.randint(100000000, 999999999)}.\n\n"
            f"Expected delivery: "
            f"{(d['inv_date'] - timedelta(days=random.randint(1, 3))).strftime('%B %d, %Y')}.\n\n"
            f"Thanks,\n{d['supplier']['name']} Logistics"
        ),
    },
    lambda d: {
        "subject": f"Payment Reminder — Invoice {d['inv_num']}",
        "body": (
            f"Dear Accounts Payable,\n\n"
            f"This is a friendly reminder that invoice {d['inv_num']} for "
            f"${d['inv_total']:,.2f} (PO: {d['po_num']}) is approaching its "
            f"due date. Please ensure timely processing.\n\n"
            f"Thank you,\n{d['supplier']['name']} Accounts Receivable"
        ),
    },
    lambda d: {
        "subject": f"Thank You for Your Order — {d['supplier']['name']}",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"Just wanted to drop a quick note to thank you for the recent order. "
            f"We value the partnership with Meridian Corp and look forward to "
            f"continuing to serve your {d['supplier']['category'].lower()} needs.\n\n"
            f"Don't hesitate to reach out if you need anything.\n\n"
            f"Best regards,\n{d['supplier']['contact_person']}\n{d['supplier']['name']}"
        ),
    },
    lambda d: {
        "subject": f"New Product Catalog — {d['supplier']['name']} Q1 2026",
        "body": (
            f"Dear {d['buyer']['name']},\n\n"
            f"Our updated Q1 2026 product catalog is now available. "
            f"We've added several new SKUs to our "
            f"{d['product_cat']['product_category']} line and adjusted pricing "
            f"on select items.\n\n"
            f"Please visit our portal or contact me directly for the latest "
            f"pricing sheet.\n\n"
            f"Best,\n{d['supplier']['contact_person']}\n{d['supplier']['name']}"
        ),
    },
    lambda d: {
        "subject": f"Delivery Confirmed — {d['po_num']}",
        "body": (
            f"Hi {_buyer_first(d['buyer']['name'])},\n\n"
            f"Just confirming that our carrier has marked {d['po_num']} as "
            f"delivered to your facility. Please let us know if there are any "
            f"issues with the shipment.\n\n"
            f"Thanks,\n{d['supplier']['contact_person']}"
        ),
    },
]

emails: list[dict] = []
email_id_counter = 1
email_link_map: dict[int, list[str]] = {}  # record_idx -> [email_ids]


def generate_exception_email(record_idx: int) -> dict:
    global email_id_counter
    d = record_details[record_idx]
    exc_type = d["exc_type"]

    if exc_type == ExceptionType.INFORMAL_MODIFICATION:
        tmpl = random.choice(INFORMAL_EMAIL_TEMPLATES)
    elif exc_type == ExceptionType.PRICE_VARIANCE:
        tmpl = random.choice(PRICE_VAR_EMAIL_TEMPLATES)
    elif exc_type == ExceptionType.QUANTITY_VARIANCE:
        tmpl = random.choice(QTY_VAR_EMAIL_TEMPLATES)
    elif exc_type == ExceptionType.MISSING_GOODS_RECEIPT:
        tmpl = random.choice(MISSING_GR_EMAIL_TEMPLATES)
    else:
        tmpl = random.choice(ROUTINE_EMAIL_TEMPLATES)

    result = tmpl(d)
    is_reversed = result.get("reverse", False)

    email_id = f"EMAIL-{email_id_counter:03d}"
    email_id_counter += 1

    if is_reversed:
        sender = d["buyer"]["email"]
        receiver = d["supplier"]["contact_email"]
    else:
        sender = d["supplier"]["contact_email"]
        receiver = d["buyer"]["email"]

    email = Email(
        email_id=email_id,
        subject=result["subject"],
        sender=sender,
        receiver=receiver,
        date=d["po_date"] + timedelta(days=random.randint(0, 5)),
        body=result["body"],
        related_po=d["po_num"],
        related_invoice=d["inv_num"],
    )

    email_link_map.setdefault(record_idx, []).append(email_id)
    return email.model_dump(mode="json")


def generate_routine_email() -> dict:
    global email_id_counter
    # Pick a random clean record for context
    clean_idx = random.choice(list(clean_indices))
    d = record_details[clean_idx]
    tmpl = random.choice(ROUTINE_EMAIL_TEMPLATES)
    result = tmpl(d)

    email_id = f"EMAIL-{email_id_counter:03d}"
    email_id_counter += 1

    email = Email(
        email_id=email_id,
        subject=result["subject"],
        sender=d["supplier"]["contact_email"],
        receiver=d["buyer"]["email"],
        date=d["po_date"] + timedelta(days=random.randint(1, 10)),
        body=result["body"],
        related_po=d["po_num"],
        related_invoice=d["inv_num"],
    )
    return email.model_dump(mode="json")


# Generate exception-linked emails
for rec_idx in email_exception_pool:
    emails.append(generate_exception_email(rec_idx))

# Fill remaining with routine emails
while len(emails) < TOTAL_EMAILS:
    emails.append(generate_routine_email())

random.shuffle(emails)

# ---------------------------------------------------------------------------
# Phone transcript generation
# ---------------------------------------------------------------------------

INFORMAL_TRANSCRIPT_TEMPLATES = [
    lambda d: (
        f"[Call between {d['buyer']['name']} (Meridian Corp) and "
        f"{d['supplier']['contact_person']} ({d['supplier']['name']})]\n"
        f"[{d['po_date'].strftime('%B %d, %Y')}, {random.randint(9, 16)}:"
        f"{random.choice(['00', '15', '30', '45'])}]\n\n"
        f"{d['supplier']['contact_person']}: Hi {_buyer_first(d['buyer']['name'])}, "
        f"this is {_supplier_first(d['supplier']['contact_person'])} from "
        f"{d['supplier']['name']}. Got a minute?\n\n"
        f"{d['buyer']['name']}: Sure, what's up?\n\n"
        f"{d['supplier']['contact_person']}: It's about your order {d['po_num']}, "
        f"the {d['base_qty']} units of {d['base_grade']['description']}. "
        f"We've got a stock situation — I can only do about "
        f"{int(d['base_qty'] * 0.8)} of the {d['base_grade']['grade']} right now.\n\n"
        f"{d['buyer']['name']}: That's a problem. We need the full quantity.\n\n"
        f"{d['supplier']['contact_person']}: I know, that's why I'm calling. "
        f"I can make up the difference with our {d['upgrade_grade']['grade']} "
        f"version. It's actually better quality. "
        f"Runs ${d['upgrade_grade']['unit_price']:.2f} per unit instead of "
        f"${d['base_grade']['unit_price']:.2f}.\n\n"
        f"{d['buyer']['name']}: What's that come to for the total order?\n\n"
        f"{d['supplier']['contact_person']}: So {int(d['base_qty'] * 0.8)} at "
        f"${d['base_grade']['unit_price']:.2f} plus "
        f"{d['base_qty'] - int(d['base_qty'] * 0.8)} at "
        f"${d['upgrade_grade']['unit_price']:.2f}. Comes to about "
        f"${d['inv_total']:,.2f} total instead of ${d['po_total']:,.2f}.\n\n"
        f"{d['buyer']['name']}: That's fine. Go ahead and ship it.\n\n"
        f"{d['supplier']['contact_person']}: Perfect, I'll get that out today. "
        f"Thanks {_buyer_first(d['buyer']['name'])}.\n\n"
        f"[Call ended]"
    ),
    lambda d: (
        f"[Call between {d['supplier']['contact_person']} ({d['supplier']['name']}) "
        f"and {d['buyer']['name']} (Meridian Corp)]\n"
        f"[{d['po_date'].strftime('%B %d, %Y')}, {random.randint(9, 16)}:"
        f"{random.choice(['00', '15', '30', '45'])}]\n\n"
        f"{d['supplier']['contact_person']}: Hey "
        f"{_buyer_first(d['buyer']['name'])}, calling about {d['po_num']}.\n\n"
        f"{d['buyer']['name']}: Yeah, is there an issue?\n\n"
        f"{d['supplier']['contact_person']}: Not a big one. We're running low "
        f"on {d['base_grade']['grade']} {d['product_cat']['product_category']}. "
        f"Our supplier had a production delay. I've got enough to cover most of "
        f"your order but I'd need to fill the rest with "
        f"{d['upgrade_grade']['grade']}.\n\n"
        f"{d['buyer']['name']}: How much more are we talking?\n\n"
        f"{d['supplier']['contact_person']}: The "
        f"{d['upgrade_grade']['grade']} is "
        f"${d['upgrade_grade']['unit_price']:.2f} versus "
        f"${d['base_grade']['unit_price']:.2f} for the "
        f"{d['base_grade']['grade']}. So on the substituted portion you're "
        f"looking at about "
        f"${d['upgrade_grade']['unit_price'] - d['base_grade']['unit_price']:.2f} "
        f"more per unit.\n\n"
        f"{d['buyer']['name']}: Alright, we can absorb that. Just make sure it "
        f"all ships together.\n\n"
        f"{d['supplier']['contact_person']}: Will do. Appreciate the flexibility.\n\n"
        f"[Call ended]"
    ),
    lambda d: (
        f"[Call between {d['buyer']['name']} (Meridian Corp) and "
        f"{d['supplier']['contact_person']} ({d['supplier']['name']})]\n"
        f"[{d['po_date'].strftime('%B %d, %Y')}, {random.randint(9, 16)}:"
        f"{random.choice(['00', '15', '30', '45'])}]\n\n"
        f"{d['buyer']['name']}: {_supplier_first(d['supplier']['contact_person'])}, "
        f"I need to talk about {d['po_num']}. We actually need this by end of "
        f"week. Can you expedite?\n\n"
        f"{d['supplier']['contact_person']}: I can try to rush it but there's "
        f"going to be a surcharge for expedited shipping. Probably around 8 to 10 "
        f"percent of the order value.\n\n"
        f"{d['buyer']['name']}: That's fine. Our production line is waiting on "
        f"this.\n\n"
        f"{d['supplier']['contact_person']}: Understood. I'll add the expedited "
        f"shipping to the invoice and get it out today.\n\n"
        f"{d['buyer']['name']}: Thanks, "
        f"{_supplier_first(d['supplier']['contact_person'])}. Appreciate it.\n\n"
        f"[Call ended]"
    ),
]

PRICE_VAR_TRANSCRIPT_TEMPLATES = [
    lambda d: (
        f"[Call between {d['supplier']['contact_person']} ({d['supplier']['name']}) "
        f"and {d['buyer']['name']} (Meridian Corp)]\n"
        f"[{d['po_date'].strftime('%B %d, %Y')}, {random.randint(9, 16)}:"
        f"{random.choice(['00', '15', '30', '45'])}]\n\n"
        f"{d['supplier']['contact_person']}: Hi "
        f"{_buyer_first(d['buyer']['name'])}, just wanted to give you a heads up "
        f"that our pricing on {d['product_cat']['product_category']} is going up "
        f"this quarter. Raw material costs have been climbing.\n\n"
        f"{d['buyer']['name']}: By how much?\n\n"
        f"{d['supplier']['contact_person']}: Somewhere around 5 to 10 percent "
        f"depending on the grade. I tried to hold it but it's a company-wide "
        f"adjustment.\n\n"
        f"{d['buyer']['name']}: Alright, we'll need to update our internal rates. "
        f"Can you send over the new price sheet?\n\n"
        f"{d['supplier']['contact_person']}: Absolutely, I'll email it over "
        f"today.\n\n"
        f"[Call ended]"
    ),
]

QTY_VAR_TRANSCRIPT_TEMPLATES = [
    lambda d: (
        f"[Call between {d['supplier']['contact_person']} ({d['supplier']['name']}) "
        f"and {d['buyer']['name']} (Meridian Corp)]\n"
        f"[{d['po_date'].strftime('%B %d, %Y')}, {random.randint(9, 16)}:"
        f"{random.choice(['00', '15', '30', '45'])}]\n\n"
        f"{d['supplier']['contact_person']}: Hey "
        f"{_buyer_first(d['buyer']['name'])}, calling about {d['po_num']}. "
        f"We had a counting issue at the warehouse and we're going to be short "
        f"on the quantity.\n\n"
        f"{d['buyer']['name']}: How short?\n\n"
        f"{d['supplier']['contact_person']}: We can ship most of it now, but "
        f"you'll be about 10 to 20 percent under. The rest should be available "
        f"next week.\n\n"
        f"{d['buyer']['name']}: Just ship what you have and invoice for what you "
        f"send. We'll do a separate PO for the backorder.\n\n"
        f"{d['supplier']['contact_person']}: Sounds good. Sorry about this.\n\n"
        f"[Call ended]"
    ),
]

ROUTINE_TRANSCRIPT_TEMPLATES = [
    lambda d: (
        f"[Call between {d['supplier']['contact_person']} ({d['supplier']['name']}) "
        f"and {d['buyer']['name']} (Meridian Corp)]\n"
        f"[{d['po_date'].strftime('%B %d, %Y')}, {random.randint(9, 16)}:"
        f"{random.choice(['00', '15', '30', '45'])}]\n\n"
        f"{d['supplier']['contact_person']}: Hi "
        f"{_buyer_first(d['buyer']['name'])}, just checking in on how things are "
        f"going. Any upcoming orders I should know about?\n\n"
        f"{d['buyer']['name']}: Actually yes, we're planning a larger order of "
        f"{d['product_cat']['product_category']} next month. I'll send the PO "
        f"over soon.\n\n"
        f"{d['supplier']['contact_person']}: Great, we'll make sure we have "
        f"inventory set aside for you.\n\n"
        f"[Call ended]"
    ),
    lambda d: (
        f"[Call between {d['buyer']['name']} (Meridian Corp) and "
        f"{d['supplier']['contact_person']} ({d['supplier']['name']})]\n"
        f"[{d['po_date'].strftime('%B %d, %Y')}, {random.randint(9, 16)}:"
        f"{random.choice(['00', '15', '30', '45'])}]\n\n"
        f"{d['buyer']['name']}: "
        f"{_supplier_first(d['supplier']['contact_person'])}, quick question — "
        f"do you have any new products in your "
        f"{d['product_cat']['product_category']} line?\n\n"
        f"{d['supplier']['contact_person']}: We actually just added a couple of "
        f"new SKUs. Want me to send over the updated catalog?\n\n"
        f"{d['buyer']['name']}: That would be great. Email it to me when you "
        f"get a chance.\n\n"
        f"{d['supplier']['contact_person']}: Will do. Talk soon.\n\n"
        f"[Call ended]"
    ),
    lambda d: (
        f"[Call between {d['supplier']['contact_person']} ({d['supplier']['name']}) "
        f"and {d['buyer']['name']} (Meridian Corp)]\n"
        f"[{d['po_date'].strftime('%B %d, %Y')}, {random.randint(9, 16)}:"
        f"{random.choice(['00', '15', '30', '45'])}]\n\n"
        f"{d['supplier']['contact_person']}: Hey "
        f"{_buyer_first(d['buyer']['name'])}, just confirming delivery for "
        f"{d['po_num']} is scheduled for "
        f"{(d['po_date'] + timedelta(days=random.randint(8, 15))).strftime('%A, %B %d')}. "
        f"Does that still work for your receiving dock?\n\n"
        f"{d['buyer']['name']}: Let me check... yes, that's fine. Morning "
        f"delivery preferred if possible.\n\n"
        f"{d['supplier']['contact_person']}: I'll note that. Thanks.\n\n"
        f"[Call ended]"
    ),
]

transcripts: list[dict] = []
transcript_id_counter = 1
transcript_link_map: dict[int, list[str]] = {}


def generate_exception_transcript(record_idx: int) -> dict:
    global transcript_id_counter
    d = record_details[record_idx]
    exc_type = d["exc_type"]

    if exc_type == ExceptionType.INFORMAL_MODIFICATION:
        tmpl = random.choice(INFORMAL_TRANSCRIPT_TEMPLATES)
    elif exc_type == ExceptionType.PRICE_VARIANCE:
        tmpl = random.choice(PRICE_VAR_TRANSCRIPT_TEMPLATES)
    elif exc_type == ExceptionType.QUANTITY_VARIANCE:
        tmpl = random.choice(QTY_VAR_TRANSCRIPT_TEMPLATES)
    else:
        # Fallback to routine for types that don't typically have calls
        clean_idx = random.choice(list(clean_indices))
        d = record_details[clean_idx]
        tmpl = random.choice(ROUTINE_TRANSCRIPT_TEMPLATES)

    transcript_text = tmpl(d)
    tid = f"CALL-{transcript_id_counter:03d}"
    transcript_id_counter += 1

    transcript = PhoneTranscript(
        transcript_id=tid,
        caller=d["supplier"]["contact_person"],
        caller_organization=d["supplier"]["name"],
        callee=d["buyer"]["name"],
        callee_organization="Meridian Corp",
        date=d["po_date"] + timedelta(days=random.randint(0, 3)),
        duration_minutes=random.randint(3, 18),
        transcript=transcript_text,
        related_po=d["po_num"],
        related_invoice=d["inv_num"],
    )

    transcript_link_map.setdefault(record_idx, []).append(tid)
    return transcript.model_dump(mode="json")


def generate_routine_transcript() -> dict:
    global transcript_id_counter
    clean_idx = random.choice(list(clean_indices))
    d = record_details[clean_idx]
    tmpl = random.choice(ROUTINE_TRANSCRIPT_TEMPLATES)
    transcript_text = tmpl(d)

    tid = f"CALL-{transcript_id_counter:03d}"
    transcript_id_counter += 1

    transcript = PhoneTranscript(
        transcript_id=tid,
        caller=d["supplier"]["contact_person"],
        caller_organization=d["supplier"]["name"],
        callee=d["buyer"]["name"],
        callee_organization="Meridian Corp",
        date=d["po_date"] + timedelta(days=random.randint(1, 10)),
        duration_minutes=random.randint(2, 12),
        transcript=transcript_text,
        related_po=d["po_num"],
        related_invoice=d["inv_num"],
    )
    return transcript.model_dump(mode="json")


# Generate exception-linked transcripts
for rec_idx in transcript_exception_pool:
    transcripts.append(generate_exception_transcript(rec_idx))

# Fill remaining with routine
while len(transcripts) < TOTAL_TRANSCRIPTS:
    transcripts.append(generate_routine_transcript())

random.shuffle(transcripts)

# ---------------------------------------------------------------------------
# Link communications back to exception records
# ---------------------------------------------------------------------------
for exc in exception_records:
    po_num = exc["po_number"]
    # Find the record index for this exception
    for idx, det in record_details.items():
        if det["po_num"] == po_num:
            if idx in email_link_map:
                exc["related_email_ids"] = email_link_map[idx]
            if idx in transcript_link_map:
                exc["related_transcript_ids"] = transcript_link_map[idx]
            break

# ---------------------------------------------------------------------------
# Save all datasets
# ---------------------------------------------------------------------------
data_dir = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(data_dir, exist_ok=True)


def save(filename: str, data):
    path = os.path.join(data_dir, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  {filename}: {len(data)} records")


print("Generating datasets...\n")
save("suppliers.json", supplier_list)
save("purchase_orders.json", purchase_orders)
save("invoices.json", invoices)
save("goods_receipts.json", goods_receipts)
save("exception_records.json", exception_records)
save("emails.json", emails)
save("phone_transcripts.json", transcripts)

# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------
exc_type_counts = {}
for exc in exception_records:
    t = exc["exception_type"]
    exc_type_counts[t] = exc_type_counts.get(t, 0) + 1

linked_emails = sum(1 for e in emails if e.get("related_po") and
                    any(exc["po_number"] == e["related_po"]
                        for exc in exception_records))
linked_transcripts = sum(1 for t in transcripts if t.get("related_po") and
                         any(exc["po_number"] == t["related_po"]
                             for exc in exception_records))

print(f"\n--- Summary ---")
print(f"Total PO-Invoice pairs: {len(purchase_orders)}")
print(f"Clean matches: {len(purchase_orders) - len(exception_records)}")
print(f"Exceptions: {len(exception_records)}")
for t, c in sorted(exc_type_counts.items()):
    print(f"  {t}: {c}")
print(f"\nEmails: {len(emails)} total ({linked_emails} linked to exceptions)")
print(f"Phone transcripts: {len(transcripts)} total ({linked_transcripts} linked to exceptions)")
print(f"Goods receipts: {len(goods_receipts)} (missing: {len(purchase_orders) - len(goods_receipts)})")
print(f"\nAll files saved to {data_dir}/")
