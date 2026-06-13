# Startup Guide — Autonomous Invoice Exception Resolution System

This guide walks through starting the complete system with Docker Compose and testing the basic flow.

---

## Prerequisites

- Docker and Docker Compose installed
- Python 3.11+ (if developing locally)
- Redis (Docker Compose handles this)
- API keys: TAVILY_API_KEY (optional), OPENAI_API_KEY (optional for real LLM)

---

## Step 1: Environment Setup

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

Edit `.env` and set:
- `TAVILY_API_KEY=tvly-...` (from tavily.com)
- `OPENAI_API_KEY=sk-...` (from openai.com; optional for demo)
- `SLACK_WEBHOOK_URL=https://hooks.slack.com/...` (optional for Slack alerts)
- `NOTIFICATION_EMAIL_TO=your-email@company.com` (optional for email alerts)
- `SAP_WEBHOOK_SECRET=your-secret-key` (change from default)

For development, the defaults work (demo LLM, no external notifications).

---

## Step 2: Start Docker Services

```bash
docker compose up -d
```

Wait for all services to be healthy:

```bash
docker compose ps
```

Expected output:
```
nocept-redis        running (port 6379, 8001)
nocept-api          running (port 8000)
nocept-webhook      running (port 8002)
nocept-worker       running (background)
nocept-dashboard    running (port 8502)
nocept-flower       running (port 5555)
```

---

## Step 3: Access the Dashboard

Open http://localhost:8502 in your browser.

This is a read-only Streamlit dashboard showing:
- Exception queue (RECEIVED/TRIAGED/RESEARCHING/PENDING_APPROVAL)
- Exception details (invoice, PO, variance, classification)
- Resolution history

**Note:** For local testing, demo users in `.users.json` are auto-created:
- Username: `admin`
- Password: `admin123`

---

## Step 4: Test the Pipeline (Minimal Test)

### 4A: Test via API directly (fastest)

```bash
# Create a test exception (simulates an invoice)
curl -X POST http://localhost:8000/tools/intake \
  -H 'Content-Type: application/json' \
  -d '{
    "invoice_number": "INV-TEST-001",
    "po_number": "PO-TEST-001",
    "grn_number": null
  }'
```

Expected response:
```json
{
  "exception_id": "exc-abc123...",
  "exception_types": ["price_variance"],
  "total_variance_usd": 500.0,
  "is_straight_through": false,
  "message": "Exception detected. Call Tool 2 (tolerance) next."
}
```

Copy `exception_id` and use it for subsequent calls:

```bash
# Check tolerance gate
curl http://localhost:8000/tools/tolerance/{exception_id}

# Check history
curl http://localhost:8000/tools/history/{exception_id}

# Check communications
curl http://localhost:8000/tools/communications/{exception_id}

# Research
curl -X POST http://localhost:8000/tools/research/{exception_id}

# Resolve
curl -X POST http://localhost:8000/tools/resolve/{exception_id} \
  -H 'Content-Type: application/json' \
  -d '{"notes": "Test resolution"}'
```

### 4B: Test via SAP Webhook (end-to-end)

1. First, send a PO webhook to create a PO in Redis:

```bash
curl -X POST http://localhost:8002/webhook/po \
  -H 'Content-Type: application/json' \
  -d '{
    "event_type": "po_updated",
    "source_system": "SAP",
    "timestamp": "2026-05-10T00:00:00Z",
    "payload": {
      "EBELN": "PO-WH-001",
      "LIFNR": "SUP-001",
      "LIFNM": "Test Supplier",
      "ERDAT": "2026-05-01",
      "ERNAM": "john.doe",
      "line_items": [
        {
          "MATNR": "SKU-001",
          "ARKTX": "Widget A",
          "MENGE": 100,
          "NETPR": 50.00,
          "NETWR": 5000.00
        }
      ],
      "NETWR": 5000.00
    }
  }'
```

2. Send an invoice webhook:

```bash
curl -X POST http://localhost:8002/webhook/invoice \
  -H 'Content-Type: application/json' \
  -d '{
    "event_type": "invoice_received",
    "source_system": "SAP",
    "timestamp": "2026-05-10T00:00:00Z",
    "payload": {
      "BELNR": "INV-001",
      "EBELN": "PO-WH-001",
      "LIFNR": "SUP-001",
      "LIFNM": "Test Supplier",
      "BLDAT": "2026-05-08",
      "WRBTR": 5100.00,
      "line_items": [
        {
          "MATNR": "SKU-001",
          "ARKTX": "Widget A",
          "MENGE": 100,
          "NETPR": 51.00,
          "NETWR": 5100.00
        }
      ]
    }
  }'
```

Expected response:
```json
{
  "status": "accepted",
  "message": "Invoice INV-001 accepted for processing",
  "exception_id": "exc-xyz789...",
  "task_id": "celery-task-id-..."
}
```

3. Check Celery task status via Flower:

Open http://localhost:5555 in your browser. You'll see the `process_exception` task running in the `ap_pipeline` queue.

4. Check exception in dashboard:

Refresh http://localhost:8502 — the exception should appear in the queue.

---

## Step 5: Monitor Logs

### API logs:
```bash
docker compose logs -f api
```

### Webhook logs:
```bash
docker compose logs -f webhook
```

### Worker logs:
```bash
docker compose logs -f worker
```

### All logs:
```bash
docker compose logs -f
```

---

## Step 6: Test Email/Slack Notifications (optional)

Set up SMTP and Slack in `.env`, then create a MISSING_GOODS_RECEIPT exception:

```bash
# Create PO and invoice without GRN
curl -X POST http://localhost:8002/webhook/invoice \
  -H 'Content-Type: application/json' \
  -d '{
    "event_type": "invoice_received",
    "source_system": "SAP",
    "timestamp": "2026-05-10T00:00:00Z",
    "payload": {
      "BELNR": "INV-002",
      "EBELN": "PO-WH-002",
      ...
    }
  }'
```

If escalated, Slack and email alerts will be sent to channels/addresses in `.env`.

---

## Step 7: Stop Services

```bash
docker compose down
```

To remove persisted data:

```bash
docker compose down -v
```

---

## Architecture Overview

| Service | Port | Purpose |
|---------|------|---------|
| **redis** | 6379, 8001 | State store, queue, RedisInsight UI |
| **api** | 8000 | Tool endpoints (intake, tolerance, history, comms, research, resolve) |
| **webhook** | 8002 | SAP webhook receiver |
| **worker** | — | Celery background tasks (runs LangGraph pipeline) |
| **dashboard** | 8502 | Streamlit read-only + approve/reject UI |
| **flower** | 5555 | Celery task monitoring |

---

## Troubleshooting

### Issue: "Exception not found"
**Cause:** Tool endpoints expect exception in Redis; might not exist yet.
**Fix:** Call `/tools/intake` first to create an exception, then use returned `exception_id` for subsequent tools.

### Issue: "PO not found" on webhook invoice
**Cause:** Invoice references a PO that doesn't exist in Redis.
**Fix:** Send PO webhook first (`/webhook/po`) before invoice (`/webhook/invoice`).

### Issue: Celery task not running
**Cause:** Worker not connecting to Redis broker.
**Fix:** Check `docker compose logs worker` for connection errors. Verify `CELERY_BROKER_URL=redis://redis:6379/1` in `.env`.

### Issue: Webhook signature verification failed
**Cause:** `X-SAP-Signature` header doesn't match payload.
**Fix:** Either:
  - Leave `SAP_WEBHOOK_SECRET` empty in `.env` to disable verification (dev only), or
  - Compute HMAC-SHA256 of body with secret and pass as header: `X-SAP-Signature: <hex>`

### Issue: Tavily or OpenAI API timeout
**Cause:** Network issue or API rate limit.
**Fix:** Check `docker compose logs worker`. Worker retries up to 3 times with exponential backoff; you'll see retry messages in logs.

---

## Next Steps

1. **Integrate real ERP:** Wire SAP production webhooks to `http://your-domain:8002/webhook/invoice|po|grn`
2. **Enable authentication:** Update dashboard to require `/auth/token` login
3. **Document API:** Visit `http://localhost:8000/docs` for interactive Swagger UI
4. **Scale workers:** Increase `celery -c` concurrency or run multiple worker containers
5. **Add monitoring:** Hook up Prometheus, Grafana, or Datadog to `/metrics` endpoint (not yet implemented)

---

## Support

Logs are in `docker compose logs <service>`. All errors are logged with traceback. Check `.env` for required credentials (Tavily, OpenAI, Slack, SMTP).

For more details, see the plan file: `CLAUDE.md`
