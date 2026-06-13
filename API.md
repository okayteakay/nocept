# API Reference: Invoice Exception Resolution System

**Version:** 1.0  
**Date:** May 13, 2026  
**Base URL:** `https://nocept-system.internal/api`  
**Documentation:** OpenAPI/Swagger at `/api/docs`

---

## Table of Contents

1. [Authentication & Authorization](#authentication--authorization)
2. [Error Handling](#error-handling)
3. [Rate Limiting](#rate-limiting)
4. [Exception Endpoints](#exception-endpoints)
5. [Approval Endpoints](#approval-endpoints)
6. [Rules Endpoints](#rules-endpoints)
7. [Analytics Endpoints](#analytics-endpoints)
8. [Webhook Endpoints](#webhook-endpoints)
9. [Data Models](#data-models)

---

## Authentication & Authorization

### **JWT Token-Based Authentication**

All API endpoints (except `/auth/login` and `/webhook/exception`) require a valid JWT token in the `Authorization` header.

**Request Header Format:**
```
Authorization: Bearer <jwt_token>
```

**Token Structure:**
```json
{
  "sub": "john.doe@company.com",
  "email": "john.doe@company.com",
  "role": "ap_manager",
  "exp": 1715594400,
  "iat": 1715570400
}
```

**Token Lifetime:** 8 hours from issuance

---

### **Obtain a Token (Login)**

**POST** `/auth/login`

**Request Body:**
```json
{
  "email": "john.doe@company.com",
  "password": "secure_password"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 28800,
  "role": "ap_manager"
}
```

**Error (401 Unauthorized):**
```json
{
  "detail": "Invalid email or password"
}
```

---

### **Role-Based Access Control**

| Role | Permissions |
|------|-------------|
| **ap_clerk** | Read exceptions, search, filter, export (no approval) |
| **ap_manager** | Approve/reject, create rules, all read permissions |
| **finance** | View analytics, KPIs, supplier scorecards (read-only) |

**Example: Manager-Only Endpoint**

```python
@app.post("/approvals/approve")
def approve_exception(
    exception_id: str,
    user: Annotated[User, Depends(get_current_user)],
    store: Store,
):
    if user.role != "ap_manager":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # ... proceed with approval
```

---

## Error Handling

### **HTTP Status Codes**

| Code | Meaning | Example |
|------|---------|---------|
| **200** | OK | Request succeeded |
| **201** | Created | Exception/rule created |
| **400** | Bad Request | Missing required field, invalid format |
| **401** | Unauthorized | Missing or invalid JWT token |
| **403** | Forbidden | Insufficient role permissions |
| **404** | Not Found | Exception ID doesn't exist |
| **409** | Conflict | Invalid state transition |
| **429** | Too Many Requests | Rate limit exceeded |
| **500** | Internal Server Error | Unhandled exception |
| **503** | Service Unavailable | Redis/LLM service down |

### **Error Response Format**

All errors return JSON with consistent structure:

```json
{
  "detail": "Human-readable error message",
  "error_code": "INVALID_STATE_TRANSITION",
  "timestamp": "2026-05-13T10:30:00Z",
  "trace_id": "req-12345-abcde"
}
```

### **Common Errors**

**Missing Authorization Header:**
```json
{
  "detail": "Not authenticated",
  "error_code": "MISSING_TOKEN"
}
```

**Expired Token:**
```json
{
  "detail": "Token expired",
  "error_code": "TOKEN_EXPIRED"
}
```

**Invalid Exception ID:**
```json
{
  "detail": "Exception not found",
  "error_code": "EXCEPTION_NOT_FOUND",
  "exception_id": "invalid-uuid"
}
```

**Invalid State Transition:**
```json
{
  "detail": "Invalid transition: APPROVED → RESEARCHING",
  "error_code": "INVALID_STATE_TRANSITION",
  "from_state": "approved",
  "to_state": "researching",
  "allowed_targets": ["resolved"]
}
```

**Insufficient Permissions:**
```json
{
  "detail": "User role 'ap_clerk' cannot approve exceptions",
  "error_code": "INSUFFICIENT_PERMISSIONS",
  "required_role": "ap_manager"
}
```

---

## Rate Limiting

**Global Limit:** 100 requests per minute per IP address

**Limit Response Header:**
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 42
X-RateLimit-Reset: 1715596800
```

**When Exceeded (429):**
```json
{
  "detail": "Rate limit exceeded. Try again in 45 seconds.",
  "error_code": "RATE_LIMIT_EXCEEDED",
  "retry_after": 45
}
```

**Reset Time:** Sliding window (resets after 1 minute of last request)

---

## Exception Endpoints

### **1. Create Exception (Webhook Ingestion)**

**POST** `/webhook/exception`

**Authentication:** None (webhook secret validation, future)

**Request Body:**
```json
{
  "invoice": {
    "invoice_number": "INV-2026-001234",
    "supplier_id": "ACME-001",
    "supplier_name": "Acme Corp",
    "invoice_date": "2026-05-10",
    "invoice_amount": 15250.00,
    "currency": "USD",
    "line_items": [
      {
        "line_number": 1,
        "sku": "ACME-WIDGET-001",
        "description": "Industrial Widget (Large)",
        "quantity": 100,
        "unit_price": 150.00,
        "line_amount": 15000.00,
        "uom": "EA"
      },
      {
        "line_number": 2,
        "sku": "SHIP-EXP",
        "description": "Expedited Shipping",
        "quantity": 1,
        "unit_price": 250.00,
        "line_amount": 250.00,
        "uom": "SVC"
      }
    ]
  },
  "purchase_order": {
    "po_number": "PO-2026-005678",
    "po_date": "2026-04-15",
    "po_amount": 15000.00,
    "line_items": [
      {
        "line_number": 1,
        "sku": "ACME-WIDGET-001",
        "description": "Industrial Widget (Large)",
        "quantity": 100,
        "unit_price": 150.00,
        "line_amount": 15000.00
      }
    ]
  },
  "goods_receipt": {
    "grn_number": "GRN-2026-009999",
    "grn_date": "2026-05-09",
    "line_items": [
      {
        "sku": "ACME-WIDGET-001",
        "quantity": 100
      }
    ]
  }
}
```

**Response (201 Created):**
```json
{
  "exception_id": "exc-550e8400-e29b-41d4-a716-446655440000",
  "invoice_number": "INV-2026-001234",
  "supplier_name": "Acme Corp",
  "exception_type": "PRICE_VARIANCE",
  "variance_usd": 250.00,
  "variance_pct": 1.67,
  "state": "received",
  "created_at": "2026-05-13T10:15:00Z"
}
```

**Error (400 Bad Request):**
```json
{
  "detail": "Missing required field: invoice.invoice_number",
  "error_code": "VALIDATION_ERROR",
  "field": "invoice.invoice_number"
}
```

---

### **2. Get Exception Detail**

**GET** `/exceptions/{exception_id}`

**Path Parameters:**
- `exception_id` (string, required): UUID of exception

**Query Parameters:**
- `include_evidence` (boolean, optional, default=true): Include linked emails, research, history

**Response (200 OK):**
```json
{
  "exception_id": "exc-550e8400-e29b-41d4-a716-446655440000",
  "invoice": {
    "invoice_number": "INV-2026-001234",
    "supplier_name": "Acme Corp",
    "invoice_date": "2026-05-10",
    "invoice_amount": 15250.00
  },
  "purchase_order": {
    "po_number": "PO-2026-005678",
    "po_amount": 15000.00
  },
  "exception_type": "PRICE_VARIANCE",
  "line_variances": [
    {
      "sku": "SHIP-EXP",
      "description": "Expedited Shipping",
      "po_quantity": null,
      "invoice_quantity": 1,
      "po_unit_price": null,
      "invoice_unit_price": 250.00,
      "quantity_delta": null,
      "price_delta_pct": null,
      "is_expedited_shipping": true,
      "is_new_sku": true
    }
  ],
  "total_variance_usd": 250.00,
  "total_variance_pct": 1.67,
  "state": "pending_approval",
  "created_at": "2026-05-13T10:15:00Z",
  "updated_at": "2026-05-13T10:30:00Z",
  "system_recommendation": {
    "action": "ESCALATE",
    "confidence": 0.0,
    "reasoning": "No gate fired. Variance outside tolerance, no supporting evidence."
  },
  "evidence": {
    "linked_emails": [
      {
        "from": "sales@acmecorp.com",
        "subject": "INV-2026-001234 expedited shipping",
        "date": "2026-05-10T14:22:00Z",
        "summary": "Expedited shipping requested by customer, additional $250 charge.",
        "relevance": "HIGH"
      }
    ],
    "linked_transcripts": [],
    "research_findings": [
      {
        "source": "acmecorp.com/pricing",
        "title": "Expedited Shipping Rate Card",
        "snippet": "Standard expedited shipping: $250 per order",
        "relevance": 0.92
      }
    ],
    "historical_precedent": [
      {
        "previous_exception_id": "exc-550e8400-e29b-41d4-a716-446655440001",
        "supplier": "Acme Corp",
        "exception_type": "PRICE_VARIANCE",
        "variance_pct": 1.5,
        "decision": "APPROVED",
        "approval_date": "2026-04-20",
        "approved_by": "jane.smith@company.com"
      }
    ]
  },
  "audit_trail": [
    {
      "timestamp": "2026-05-13T10:15:00Z",
      "action": "EXCEPTION_CREATED",
      "actor": "system",
      "details": "Webhook ingestion from SAP"
    },
    {
      "timestamp": "2026-05-13T10:30:00Z",
      "action": "STATE_TRANSITION",
      "actor": "system",
      "details": "TRIAGED → PENDING_APPROVAL (escalated)"
    }
  ]
}
```

---

### **3. List Exceptions (Search & Filter)**

**GET** `/exceptions`

**Query Parameters:**
```
exception_id: string (optional) - UUID filter
invoice_number: string (optional) - Partial match
supplier: string (optional) - Partial match
exception_type: string (optional) - PRICE_VARIANCE, QTY_MISMATCH, DUPLICATE, etc.
state: string (optional) - received, triaged, pending_approval, approved, rejected, resolved
variance_min: float (optional) - Minimum variance USD
variance_max: float (optional) - Maximum variance USD
created_after: datetime (optional) - ISO 8601 format
created_before: datetime (optional) - ISO 8601 format
page: integer (optional, default=1) - Pagination page number
page_size: integer (optional, default=20, max=100) - Items per page
sort_by: string (optional, default=created_at) - Field to sort on
sort_order: string (optional, default=desc) - asc or desc
```

**Example Request:**
```
GET /exceptions?supplier=Acme&state=pending_approval&variance_min=100&page=1&page_size=10
Authorization: Bearer <token>
```

**Response (200 OK):**
```json
{
  "total": 87,
  "page": 1,
  "page_size": 10,
  "pages": 9,
  "exceptions": [
    {
      "exception_id": "exc-550e8400-e29b-41d4-a716-446655440000",
      "invoice_number": "INV-2026-001234",
      "supplier_name": "Acme Corp",
      "exception_type": "PRICE_VARIANCE",
      "variance_usd": 250.00,
      "variance_pct": 1.67,
      "state": "pending_approval",
      "created_at": "2026-05-13T10:15:00Z"
    },
    {
      "exception_id": "exc-550e8400-e29b-41d4-a716-446655440001",
      "invoice_number": "INV-2026-001235",
      "supplier_name": "Widget Inc",
      "exception_type": "QUANTITY_VARIANCE",
      "variance_usd": 500.00,
      "variance_pct": 5.0,
      "state": "escalated",
      "created_at": "2026-05-13T09:45:00Z"
    }
  ]
}
```

---

### **4. Export Exceptions (CSV)**

**GET** `/exceptions/export`

**Query Parameters:**
```
format: string (optional, default=csv) - csv or xlsx
supplier: string (optional) - Filter to supplier
state: string (optional) - Filter by state
created_after: datetime (optional)
created_before: datetime (optional)
```

**Response (200 OK - Content-Type: text/csv):**
```csv
exception_id,invoice_number,supplier,exception_type,variance_usd,variance_pct,state,created_at
exc-550e8400-e29b-41d4-a716-446655440000,INV-2026-001234,Acme Corp,PRICE_VARIANCE,250.00,1.67,pending_approval,2026-05-13T10:15:00Z
exc-550e8400-e29b-41d4-a716-446655440001,INV-2026-001235,Widget Inc,QUANTITY_VARIANCE,500.00,5.0,escalated,2026-05-13T09:45:00Z
...
```

---

## Approval Endpoints

### **1. Approve Exception**

**POST** `/approvals/approve`

**Required Role:** `ap_manager`

**Request Body:**
```json
{
  "exception_id": "exc-550e8400-e29b-41d4-a716-446655440000",
  "notes": "Confirmed with supplier, expedited shipping was authorized.",
  "create_rule": {
    "enabled": true,
    "rule_name": "Acme Auto-Approval",
    "supplier": "Acme Corp",
    "exception_types": ["PRICE_VARIANCE"],
    "variance_min_pct": 1.0,
    "variance_max_pct": 3.0,
    "max_amount_usd": 10000.00
  }
}
```

**Response (200 OK):**
```json
{
  "exception_id": "exc-550e8400-e29b-41d4-a716-446655440000",
  "state": "approved",
  "approved_by": "john.doe@company.com",
  "approval_notes": "Confirmed with supplier, expedited shipping was authorized.",
  "approval_timestamp": "2026-05-13T14:30:00Z",
  "rule_created": {
    "rule_id": "rule-123456",
    "rule_name": "Acme Auto-Approval",
    "created_at": "2026-05-13T14:30:00Z"
  }
}
```

**Error (403 Forbidden):**
```json
{
  "detail": "User role 'ap_clerk' cannot approve exceptions",
  "error_code": "INSUFFICIENT_PERMISSIONS"
}
```

**Error (409 Conflict):**
```json
{
  "detail": "Invalid transition: APPROVED → APPROVED",
  "error_code": "INVALID_STATE_TRANSITION"
}
```

---

### **2. Reject Exception**

**POST** `/approvals/reject`

**Required Role:** `ap_manager`

**Request Body:**
```json
{
  "exception_id": "exc-550e8400-e29b-41d4-a716-446655440000",
  "reason": "Variance not authorized. PO locked at $15,000. Supplier must resubmit.",
  "notify_supplier": true
}
```

**Response (200 OK):**
```json
{
  "exception_id": "exc-550e8400-e29b-41d4-a716-446655440000",
  "state": "rejected",
  "rejected_by": "john.doe@company.com",
  "rejection_reason": "Variance not authorized. PO locked at $15,000. Supplier must resubmit.",
  "rejection_timestamp": "2026-05-13T14:35:00Z",
  "supplier_notification": {
    "sent": true,
    "method": "email",
    "sent_at": "2026-05-13T14:35:05Z"
  }
}
```

---

## Rules Endpoints

### **1. Create Rule**

**POST** `/rules`

**Required Role:** `ap_manager`

**Request Body:**
```json
{
  "rule_name": "Acme Corp Auto-Approval",
  "description": "Auto-approve price variances for Acme Corp within agreed range",
  "supplier": "Acme Corp",
  "exception_types": ["PRICE_VARIANCE"],
  "variance_min_pct": 1.0,
  "variance_max_pct": 3.0,
  "max_amount_usd": 10000.00,
  "conditions": {
    "require_communication": false,
    "require_historical_precedent": false
  },
  "action": "AUTO_APPROVE",
  "enabled": true
}
```

**Response (201 Created):**
```json
{
  "rule_id": "rule-550e8400-e29b-41d4-a716-446655440000",
  "rule_name": "Acme Corp Auto-Approval",
  "supplier": "Acme Corp",
  "created_by": "john.doe@company.com",
  "created_at": "2026-05-13T14:40:00Z",
  "enabled": true,
  "usage": {
    "auto_approvals_count": 0,
    "auto_rejections_count": 0,
    "last_fired": null
  }
}
```

---

### **2. List Rules**

**GET** `/rules`

**Query Parameters:**
```
supplier: string (optional)
exception_type: string (optional)
enabled: boolean (optional)
page: integer (optional, default=1)
page_size: integer (optional, default=20)
```

**Response (200 OK):**
```json
{
  "total": 5,
  "page": 1,
  "page_size": 20,
  "rules": [
    {
      "rule_id": "rule-550e8400-e29b-41d4-a716-446655440000",
      "rule_name": "Acme Corp Auto-Approval",
      "supplier": "Acme Corp",
      "exception_types": ["PRICE_VARIANCE"],
      "action": "AUTO_APPROVE",
      "enabled": true,
      "created_by": "john.doe@company.com",
      "created_at": "2026-05-13T14:40:00Z",
      "usage": {
        "auto_approvals_count": 12,
        "auto_rejections_count": 0,
        "last_fired": "2026-05-13T10:15:00Z"
      }
    }
  ]
}
```

---

### **3. Update Rule**

**PUT** `/rules/{rule_id}`

**Required Role:** `ap_manager`

**Request Body:**
```json
{
  "variance_min_pct": 0.5,
  "variance_max_pct": 5.0,
  "max_amount_usd": 15000.00,
  "enabled": true
}
```

**Response (200 OK):**
```json
{
  "rule_id": "rule-550e8400-e29b-41d4-a716-446655440000",
  "rule_name": "Acme Corp Auto-Approval",
  "variance_min_pct": 0.5,
  "variance_max_pct": 5.0,
  "max_amount_usd": 15000.00,
  "updated_at": "2026-05-13T15:00:00Z"
}
```

---

### **4. Delete Rule**

**DELETE** `/rules/{rule_id}`

**Required Role:** `ap_manager`

**Response (204 No Content):**
```
(empty body)
```

**Note:** System prevents deletion if rule has active usage (prevent history loss). Use "Disable" instead.

---

## Analytics Endpoints

### **1. Get KPI Dashboard**

**GET** `/analytics/kpis`

**Query Parameters:**
```
date_from: datetime (optional, default=30 days ago)
date_to: datetime (optional, default=today)
supplier: string (optional)
exception_type: string (optional)
```

**Response (200 OK):**
```json
{
  "period": {
    "from": "2026-04-13",
    "to": "2026-05-13"
  },
  "summary": {
    "total_exceptions": 142,
    "auto_resolved": 102,
    "auto_resolution_rate": 71.83,
    "manual_approved": 28,
    "manual_approval_rate": 19.72,
    "rejected": 12,
    "rejection_rate": 8.45,
    "escalated": 24,
    "escalation_rate": 16.9
  },
  "financial_impact": {
    "cost_at_risk_usd": 87450.00,
    "cost_saved_usd": 18200.00,
    "cost_of_exceptions_usd": 69250.00,
    "roi": 8.2
  },
  "efficiency": {
    "avg_resolution_hours": 2.3,
    "sla_compliance_pct": 96.5,
    "manual_review_hours_saved": 47.5
  },
  "trend": {
    "exceptions_per_day": 4.73,
    "auto_approval_rate_trend": "stable",
    "cost_saved_trend": "increasing"
  }
}
```

---

### **2. Get Supplier Scorecards**

**GET** `/analytics/suppliers`

**Query Parameters:**
```
date_from: datetime (optional, default=30 days ago)
date_to: datetime (optional, default=today)
sort_by: string (optional, default=exception_rate) - exception_rate, variance_avg, invoice_count
sort_order: string (optional, default=desc)
page: integer (optional, default=1)
page_size: integer (optional, default=10)
```

**Response (200 OK):**
```json
{
  "total": 47,
  "page": 1,
  "page_size": 10,
  "scorecards": [
    {
      "supplier_name": "Acme Corp",
      "invoice_count": 87,
      "exception_count": 12,
      "exception_rate_pct": 13.79,
      "exception_types": {
        "PRICE_VARIANCE": 6,
        "QUANTITY_VARIANCE": 4,
        "DUPLICATE": 2,
        "OTHER": 0
      },
      "variance_stats": {
        "avg_variance_usd": 287.50,
        "avg_variance_pct": 2.1,
        "min_variance_usd": 50.00,
        "max_variance_usd": 1200.00
      },
      "trend": {
        "exceptions_last_month": 4,
        "exceptions_this_month": 8,
        "trend_direction": "worsening"
      },
      "risk_level": "HIGH",
      "recommendation": "Schedule vendor review meeting"
    },
    {
      "supplier_name": "Widget Inc",
      "invoice_count": 156,
      "exception_count": 7,
      "exception_rate_pct": 4.49,
      "exception_types": {
        "PRICE_VARIANCE": 3,
        "QUANTITY_VARIANCE": 2,
        "DUPLICATE": 2,
        "OTHER": 0
      },
      "variance_stats": {
        "avg_variance_usd": 165.00,
        "avg_variance_pct": 1.2,
        "min_variance_usd": 25.00,
        "max_variance_usd": 500.00
      },
      "trend": {
        "exceptions_last_month": 5,
        "exceptions_this_month": 2,
        "trend_direction": "improving"
      },
      "risk_level": "MEDIUM",
      "recommendation": "Continue monitoring"
    }
  ]
}
```

---

### **3. Get Spend Variance Report**

**GET** `/analytics/spend-variance`

**Query Parameters:**
```
date_from: datetime (optional)
date_to: datetime (optional)
group_by: string (optional, default=exception_type) - exception_type or supplier
```

**Response (200 OK):**
```json
{
  "period": {
    "from": "2026-04-13",
    "to": "2026-05-13"
  },
  "total_variance_usd": 87450.00,
  "breakdown_by_type": [
    {
      "exception_type": "PRICE_VARIANCE",
      "count": 47,
      "variance_usd": 47300.00,
      "variance_pct": 54.1,
      "avg_variance_per_exception": 1006.38,
      "approved_count": 35,
      "approved_amount": 35100.00,
      "rejected_count": 12,
      "rejected_amount": 12200.00
    },
    {
      "exception_type": "QUANTITY_VARIANCE",
      "count": 23,
      "variance_usd": 23100.00,
      "variance_pct": 26.4,
      "avg_variance_per_exception": 1004.35,
      "approved_count": 18,
      "approved_amount": 18000.00,
      "rejected_count": 5,
      "rejected_amount": 5100.00
    }
  ],
  "breakdown_by_supplier": [
    {
      "supplier": "Acme Corp",
      "variance_usd": 12300.00,
      "variance_pct": 14.1,
      "count": 12
    }
  ],
  "trend": [
    {
      "period": "2026-04-13 to 2026-04-19",
      "variance_usd": 18000.00
    },
    {
      "period": "2026-04-20 to 2026-04-26",
      "variance_usd": 21000.00
    },
    {
      "period": "2026-05-13 to 2026-05-13",
      "variance_usd": 23100.00
    }
  ]
}
```

---

## Webhook Endpoints

### **SAP S/4HANA Invoice Webhook**

**POST** `/webhook/exception`

**Authentication:** HMAC signature validation (future)

**Request Header:**
```
X-Webhook-Signature: sha256=<hex_digest>
X-Webhook-Timestamp: 2026-05-13T10:15:00Z
```

**Request Body:**
```json
{
  "event_type": "INVOICE_CREATED",
  "timestamp": "2026-05-13T10:15:00Z",
  "source": "SAP_S4HANA",
  "invoice": {
    "invoice_number": "INV-2026-001234",
    "supplier_id": "ACME-001",
    "supplier_name": "Acme Corp",
    "invoice_date": "2026-05-10",
    "invoice_amount": 15250.00,
    "currency": "USD",
    "line_items": [...]
  },
  "purchase_order": {
    "po_number": "PO-2026-005678",
    ...
  },
  "goods_receipt": {
    "grn_number": "GRN-2026-009999",
    ...
  }
}
```

**Response (202 Accepted):**
```json
{
  "status": "accepted",
  "exception_id": "exc-550e8400-e29b-41d4-a716-446655440000",
  "message": "Exception queued for processing"
}
```

**Idempotency:** Webhook can be called multiple times with same invoice. System deduplicates by `invoice_number + supplier_id + invoice_date`.

---

## Data Models

### **Exception**

```json
{
  "exception_id": "string (UUID)",
  "invoice": "Invoice object",
  "purchase_order": "PurchaseOrder object",
  "goods_receipt": "GoodsReceiptNote object (optional)",
  "exception_types": ["string enum (PRICE_VARIANCE, QUANTITY_VARIANCE, DUPLICATE_INVOICE, ...)"],
  "state": "string enum (received, triaged, researching, pending_approval, approved, rejected, resolved, escalated)",
  "line_variances": [
    {
      "sku": "string",
      "description": "string",
      "po_quantity": "integer (optional)",
      "invoice_quantity": "integer (optional)",
      "po_unit_price": "float (optional)",
      "invoice_unit_price": "float (optional)",
      "quantity_delta": "integer (optional)",
      "price_delta_pct": "float (optional)",
      "is_new_sku": "boolean",
      "is_expedited_shipping": "boolean"
    }
  ],
  "total_variance_usd": "float",
  "created_at": "datetime (ISO 8601)",
  "updated_at": "datetime (ISO 8601)",
  "approved_by": "string (email, optional)",
  "approval_notes": "string (optional)",
  "approval_timestamp": "datetime (optional)",
  "rejected_by": "string (email, optional)",
  "rejection_reason": "string (optional)",
  "rejection_timestamp": "datetime (optional)"
}
```

### **Invoice**

```json
{
  "invoice_number": "string",
  "supplier_id": "string",
  "supplier_name": "string",
  "invoice_date": "date (YYYY-MM-DD)",
  "invoice_amount": "float",
  "currency": "string (USD, EUR, etc.)",
  "line_items": [
    {
      "line_number": "integer",
      "sku": "string",
      "description": "string",
      "quantity": "float",
      "unit_price": "float",
      "line_amount": "float",
      "uom": "string (EA, SVC, KG, etc.)"
    }
  ]
}
```

### **PurchaseOrder**

```json
{
  "po_number": "string",
  "po_date": "date",
  "po_amount": "float",
  "line_items": [
    {
      "line_number": "integer",
      "sku": "string",
      "description": "string",
      "quantity": "float",
      "unit_price": "float",
      "line_amount": "float"
    }
  ]
}
```

### **Rule**

```json
{
  "rule_id": "string (UUID)",
  "rule_name": "string",
  "description": "string (optional)",
  "supplier": "string (optional, null = all suppliers)",
  "exception_types": ["string enum"],
  "variance_min_pct": "float (optional)",
  "variance_max_pct": "float (optional)",
  "max_amount_usd": "float (optional)",
  "conditions": {
    "require_communication": "boolean",
    "require_historical_precedent": "boolean"
  },
  "action": "string enum (AUTO_APPROVE, AUTO_REJECT, ESCALATE)",
  "enabled": "boolean",
  "created_by": "string (email)",
  "created_at": "datetime"
}
```

---

## Common Workflows

### **Workflow 1: Ingest and Auto-Approve**

```
1. POST /webhook/exception
   ├─ Request: {invoice, po, grn}
   └─ Response: 202 Accepted, exception_id

2. System processes (async):
   ├─ Classify exception
   ├─ Gate 1: Duplicate? → No
   ├─ Gate 2: Tolerance ≤ 1%? → Yes, AUTO_APPROVE
   └─ Exception state: RESOLVED

3. GET /exceptions/{exception_id}
   └─ Response: state=resolved, confidence=1.0
```

### **Workflow 2: Ingest, Escalate, Manager Approves**

```
1. POST /webhook/exception
   └─ Response: 202 Accepted, exception_id

2. System processes (async):
   ├─ Classify exception
   ├─ Gates 1–5: No fires
   └─ Gate 6: Escalate → state=pending_approval

3. GET /exceptions?state=pending_approval
   └─ Response: List of exceptions awaiting approval

4. GET /exceptions/{exception_id}
   └─ Response: Full detail with evidence (emails, research, history)

5. POST /approvals/approve
   ├─ Request: {exception_id, notes, create_rule: {...}}
   └─ Response: state=approved, rule_created

6. POST /rules (if rule doesn't exist)
   └─ Future: Similar exceptions auto-approve via rule
```

### **Workflow 3: Generate Analytics Report**

```
1. GET /analytics/kpis?date_from=2026-04-13&date_to=2026-05-13
   └─ Response: KPI summary (total, rates, cost)

2. GET /analytics/suppliers
   └─ Response: Top 10 suppliers by exception rate

3. GET /analytics/spend-variance?group_by=supplier
   └─ Response: Spend breakdown by supplier

4. GET /exceptions/export?created_after=2026-04-13
   └─ Response: CSV file (download)
```

---

## Best Practices

### **Rate Limiting Strategy**

- Batch requests where possible (e.g., load 50 exceptions per page)
- Cache KPI results for 5 minutes (reduce repeated calculations)
- Avoid polling webhooks; use server-sent events (future)

### **Error Handling in Client**

```python
def approve_exception(exception_id, notes):
    try:
        response = requests.post(
            f"{BASE_URL}/approvals/approve",
            json={"exception_id": exception_id, "notes": notes},
            headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as e:
        if e.response.status_code == 401:
            # Token expired, refresh and retry
            refresh_token()
            return approve_exception(exception_id, notes)
        elif e.response.status_code == 409:
            # Invalid state transition, notify user
            raise ValueError(f"Cannot approve: {e.response.json()['detail']}")
        else:
            raise
```

### **Webhook Security**

- Always validate HMAC signature (future implementation)
- Idempotent processing (handle duplicate webhooks)
- Log all webhook events for audit trail
- Implement retry logic with exponential backoff

---

## Support

**API Documentation:** https://nocept-system.internal/api/docs (OpenAPI/Swagger)

**Support Email:** api-support@nocept-system.internal

**Response Time:** 
- Urgent issues: 1 hour
- Normal questions: 24 hours

---

**Document prepared:** May 13, 2026  
**For:** Invoice Exception Resolution System (Nocept)
