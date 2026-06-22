# API Reference: Invoice Exception Resolution System

**Version:** 5.0 (Simplified)  
**Date:** June 2026  
**Base URL:** `http://localhost:8000`  
**Documentation:** Interactive OpenAPI/Swagger at `/docs`

---

## Table of Contents

1. [Unified Ingestion](#unified-ingestion)
2. [Exception Management](#exception-management)
3. [Error Handling](#error-handling)
4. [Data Models](#data-models)

---

## Unified Ingestion

### `POST /ingest`

Unified endpoint for ingesting invoices, POs, and GRNs in any format (JSON, text, image, PDF).

**Request:**

```json
{
  "doc_type": "invoice",
  "format": "json",
  "data": {
    "invoice_number": "INV-001",
    "supplier_id": "SUP-001",
    "supplier_name": "Apex Corp",
    "po_number": "PO-001",
    "invoice_date": "2026-06-01",
    "due_date": "2026-07-01",
    "payment_terms": "Net 30",
    "currency": "USD",
    "line_items": [
      {
        "sku": "SKU-123",
        "description": "Widget",
        "quantity": 100,
        "unit_price": 10.00,
        "total": 1000.00
      }
    ],
    "total_amount": 1000.00
  },
  "po_number": "PO-001"
}
```

**Parameters:**

| Field | Type | Required | Description |
|---|---|---|---|
| `doc_type` | string | Yes | One of: `invoice`, `po`, `grn` |
| `format` | string | Yes | One of: `json`, `text`, `image`, `pdf` |
| `data` | string \| dict \| bytes | Yes | Document data (dict for JSON, string for text, base64 string for image/PDF) |
| `po_number` | string | No | PO number (required for GRN, optional for invoice) |

**Response (Invoice — 202 Accepted):**

```json
{
  "status": "accepted",
  "message": "Invoice INV-001 accepted for processing",
  "exception_id": "EXC-12345"
}
```

The pipeline runs asynchronously in the background. Poll `/exceptions/list` to track status.

**Response (PO — 200 OK):**

```json
{
  "status": "stored",
  "message": "PO PO-001 received and cached"
}
```

**Response (GRN — 200 OK):**

```json
{
  "status": "stored",
  "message": "GRN GR-001 received and cached; re-triggered 2 exception(s)"
}
```

**Error Responses:**

| Status | Body | Meaning |
|---|---|---|
| 400 | `{"detail": "doc_type must be one of: ..."}` | Invalid doc_type |
| 422 | `{"detail": "PO POO-001 not found. Call /ingest with PO first."}` | Invoice references non-existent PO |
| 422 | `{"detail": "LLM extraction resulted in invalid invoice data"}` | LLM parsing failed or returned invalid structure |
| 500 | `{"detail": "Failed to cache PO"}` | Redis error |

---

## Exception Management

### `POST /tools/approve/{exception_id}`

Manually approve an escalated exception.

**Request:**

```json
{
  "approved_by": "john.doe@company.com",
  "notes": "Confirmed with supplier. Price increase approved."
}
```

**Response (200 OK):**

```json
{
  "exception_id": "EXC-12345",
  "status": "approved",
  "message": "Exception approved by john.doe@company.com. Notes: Confirmed with supplier..."
}
```

**Error Responses:**

| Status | Body | Meaning |
|---|---|---|
| 404 | `{"detail": "not found"}` | Exception ID doesn't exist |
| 400 | `{"detail": "Cannot approve exception in state 'RECEIVED'. ..."}` | Exception must be in ESCALATED or PENDING_APPROVAL state |

---

### `POST /tools/reject/{exception_id}`

Manually reject an escalated exception.

**Request:**

```json
{
  "rejected_by": "jane.smith@company.com",
  "reason": "Price increase exceeds policy threshold."
}
```

**Response (200 OK):**

```json
{
  "exception_id": "EXC-12345",
  "status": "rejected",
  "message": "Exception rejected by jane.smith@company.com. Reason: Price increase exceeds..."
}
```

---

### `POST /exceptions/list`

Search and filter exceptions by multiple criteria.

**Request:**

```json
{
  "supplier_id": "SUP-001",
  "supplier_name": null,
  "invoice_number": null,
  "po_number": "PO-001",
  "status": "ESCALATED",
  "variance_min": 100.00,
  "variance_max": 5000.00,
  "limit": 50,
  "offset": 0
}
```

**Parameters:**

| Field | Type | Default | Description |
|---|---|---|---|
| `supplier_id` | string | null | Filter by supplier ID (exact match) |
| `supplier_name` | string | null | Filter by supplier name (substring match) |
| `invoice_number` | string | null | Filter by invoice number (exact match) |
| `po_number` | string | null | Filter by PO number (exact match) |
| `status` | string | null | Filter by exception state (RECEIVED, TRIAGED, ESCALATED, APPROVED, REJECTED, RESOLVED) |
| `variance_min` | float | null | Minimum variance USD |
| `variance_max` | float | null | Maximum variance USD |
| `limit` | int | 50 | Results per page (1–1000) |
| `offset` | int | 0 | Pagination offset |

**Response (200 OK):**

```json
{
  "exceptions": [
    {
      "exception_id": "EXC-12345",
      "invoice_number": "INV-001",
      "po_number": "PO-001",
      "supplier_name": "Apex Corp",
      "supplier_id": "SUP-001",
      "exception_types": ["price_variance"],
      "total_variance_usd": 250.50,
      "variance_percentage": 2.5,
      "state": "ESCALATED",
      "created_at": "2026-06-15T10:30:00Z",
      "approved_by": null,
      "rejected_by": null
    }
  ],
  "total_count": 42,
  "limit": 50,
  "offset": 0
}
```

---

## Error Handling

### Standard Error Response

All errors return a consistent format:

```json
{
  "detail": "Human-readable error message"
}
```

### Common Status Codes

| Status | Meaning |
|---|---|
| 200 | Success (synchronous operation) |
| 202 | Accepted (async operation in progress — check status later) |
| 400 | Bad request (invalid parameters) |
| 404 | Not found (resource doesn't exist) |
| 422 | Unprocessable entity (validation failed) |
| 500 | Server error (internal exception) |

### LLM Extraction Errors

If the LLM normalizer fails:

```json
{
  "detail": "Failed to normalize invoice: LLM returned invalid JSON"
}
```

**Causes:**
- Model timeout or unavailable
- Non-conforming JSON that LLM couldn't parse
- Image too poor quality for vision model
- Invalid model credentials

---

## Data Models

### **IngestRequest**

```python
class IngestRequest(BaseModel):
    doc_type: Literal["invoice", "po", "grn"]
    format: Literal["json", "text", "image", "pdf"]
    data: Union[str, dict, bytes]
    po_number: Optional[str] = None
```

### **IngestResponse**

```python
class IngestResponse(BaseModel):
    status: str  # "accepted" | "stored" | "retriggered"
    message: str
    exception_id: Optional[str] = None  # only for invoice
```

### **ApprovalRequest**

```python
class ApprovalRequest(BaseModel):
    approved_by: str  # user email or ID
    notes: Optional[str] = None
```

### **RejectionRequest**

```python
class RejectionRequest(BaseModel):
    rejected_by: str
    reason: str
```

### **ExceptionSummary**

```python
class ExceptionSummary(BaseModel):
    exception_id: str
    invoice_number: str
    po_number: str
    supplier_name: str
    supplier_id: str
    exception_types: list[str]  # ["price_variance", ...]
    total_variance_usd: float
    variance_percentage: float
    state: str  # RECEIVED | TRIAGED | ESCALATED | APPROVED | REJECTED | RESOLVED
    created_at: datetime
    approved_by: Optional[str] = None
    rejected_by: Optional[str] = None
```

### **ExceptionListResponse**

```python
class ExceptionListResponse(BaseModel):
    exceptions: list[ExceptionSummary]
    total_count: int
    limit: int
    offset: int
```

---

## Examples

### Example 1: Ingest a JSON Invoice

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "doc_type": "invoice",
    "format": "json",
    "data": {
      "invoice_number": "INV-12345",
      "supplier_id": "SUP-001",
      "supplier_name": "Apex Corp",
      "po_number": "PO-5678",
      "invoice_date": "2026-06-15",
      "due_date": "2026-07-15",
      "payment_terms": "Net 30",
      "currency": "USD",
      "line_items": [
        {
          "sku": "WIDGET-A",
          "description": "Standard Widget",
          "product_grade": "Standard",
          "quantity": 100,
          "unit_price": 12.50,
          "total": 1250.00
        }
      ],
      "total_amount": 1250.00
    },
    "po_number": "PO-5678"
  }'
```

**Response:**
```json
{
  "status": "accepted",
  "message": "Invoice INV-12345 accepted for processing",
  "exception_id": "EXC-abc123"
}
```

### Example 2: Ingest a PO

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "doc_type": "po",
    "format": "json",
    "data": {
      "po_number": "PO-5678",
      "supplier_id": "SUP-001",
      "supplier_name": "Apex Corp",
      "created_by": "buyer@company.com",
      "creation_date": "2026-06-01",
      "department": "Procurement",
      "cost_center": "CC-100",
      "currency": "USD",
      "line_items": [
        {
          "sku": "WIDGET-A",
          "description": "Standard Widget",
          "product_grade": "Standard",
          "quantity": 100,
          "unit_price": 10.00,
          "total": 1000.00
        }
      ],
      "total_amount": 1000.00
    }
  }'
```

**Response:**
```json
{
  "status": "stored",
  "message": "PO PO-5678 received and cached"
}
```

### Example 3: Approve an Exception

```bash
curl -X POST http://localhost:8000/tools/approve/EXC-abc123 \
  -H "Content-Type: application/json" \
  -d '{
    "approved_by": "manager@company.com",
    "notes": "Confirmed with supplier. Price increase approved due to supply chain disruption."
  }'
```

**Response:**
```json
{
  "exception_id": "EXC-abc123",
  "status": "approved",
  "message": "Exception approved by manager@company.com. Notes: Confirmed with supplier..."
}
```

### Example 4: List Escalated Exceptions

```bash
curl -X POST http://localhost:8000/exceptions/list \
  -H "Content-Type: application/json" \
  -d '{
    "status": "ESCALATED",
    "limit": 10,
    "offset": 0
  }'
```

**Response:**
```json
{
  "exceptions": [
    {
      "exception_id": "EXC-abc123",
      "invoice_number": "INV-12345",
      "po_number": "PO-5678",
      "supplier_name": "Apex Corp",
      "supplier_id": "SUP-001",
      "exception_types": ["price_variance"],
      "total_variance_usd": 250.00,
      "variance_percentage": 25.0,
      "state": "ESCALATED",
      "created_at": "2026-06-15T10:30:00Z",
      "approved_by": null,
      "rejected_by": null
    }
  ],
  "total_count": 1,
  "limit": 10,
  "offset": 0
}
```

---

## Health Check

### `GET /health`

Liveness probe endpoint.

**Response (200 OK):**

```json
{
  "status": "ok"
}
```

---

## Implementation Notes

### Asynchronous Processing

When an invoice is ingested, the response is **202 Accepted** and includes an `exception_id`. The pipeline runs in the background via FastAPI `BackgroundTasks`.

To check the status of an exception, use `POST /exceptions/list` with the `exception_id` or query by invoice/PO number.

### Document Parsing

- **JSON**: Validated directly against Pydantic model. If validation fails, falls back to LLM extraction.
- **Text**: Sent to LLM with extraction prompt
- **Image**: Base64-encoded, sent to vision-capable LLM
- **PDF**: Converted to images via `pdf2image`, then sent to vision LLM

All formats must include the required fields for the document type (invoice, PO, or GRN).

### PO/GRN Caching

POs and GRNs are cached in Redis with a 30-day TTL. When a GRN is ingested for a PO that has invoices with `MISSING_GOODS_RECEIPT` exceptions, those exceptions are automatically re-triggered.

### Audit Trail

All operations are logged to Redis Streams for compliance. Query via:
```bash
redis-cli
> XRANGE ap:audit:events - +
```
