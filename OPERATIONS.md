# Operations Guide

Running, deploying, monitoring, and troubleshooting the Invoice Exception Resolution system in production.

---

## Local Development (5 minutes)

### Prerequisites
- Python 3.11+
- Docker + Docker Compose
- API key: `OPENAI_API_KEY` (must be vision-capable, e.g., gpt-4o-mini)

### Start everything

```bash
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY and OPENAI_MODEL
docker compose up -d
```

This brings up two services:

| Service    | Port | Purpose |
|------------|------|---------|
| `redis`    | 6379 | State store, audit trail, PO/GRN cache |
| `api`      | 8000 | FastAPI (unified ingest, approvals, search) |

### Verify it works

```bash
# Health check
curl http://localhost:8000/health

# Interactive API docs
open http://localhost:8000/docs

# Ingest a sample PO
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "doc_type": "po",
    "format": "json",
    "data": {
      "po_number": "PO-TEST-001",
      "supplier_id": "SUP-001",
      "supplier_name": "Test Corp",
      "created_by": "user@example.com",
      "creation_date": "2026-06-01",
      "department": "Procurement",
      "cost_center": "CC-100",
      "currency": "USD",
      "line_items": [{"sku": "SKU-001", "description": "Widget", "product_grade": "Standard", "quantity": 100, "unit_price": 10.0, "total": 1000.0}],
      "total_amount": 1000.0
    }
  }'

# Ingest an invoice
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "doc_type": "invoice",
    "format": "json",
    "data": {
      "invoice_number": "INV-TEST-001",
      "supplier_id": "SUP-001",
      "supplier_name": "Test Corp",
      "po_number": "PO-TEST-001",
      "invoice_date": "2026-06-15",
      "due_date": "2026-07-15",
      "payment_terms": "Net 30",
      "currency": "USD",
      "line_items": [{"sku": "SKU-001", "description": "Widget", "product_grade": "Standard", "quantity": 100, "unit_price": 10.0, "total": 1000.0}],
      "total_amount": 1000.0
    },
    "po_number": "PO-TEST-001"
  }'

# List exceptions (will show RECEIVED)
curl -X POST http://localhost:8000/exceptions/list \
  -H "Content-Type: application/json" \
  -d '{"limit": 10}'
```

After ~5-10 seconds, exception should transition to RESOLVED (auto-approved via tolerance gate).

---

## Production Deployment

### Stack

- Python 3.11 (slim Docker base)
- Redis 7+ (state, audit, cache — no Celery broker)
- 2 Docker services: `redis` + `api`

### Pre-flight

1. **Provision Redis**
   - Single instance or cluster
   - 2GB+ memory (depends on exception volume)
   - Persistence: RDB snapshots at least daily
   - Optional: Redis Sentinel for HA

2. **API Key Management**
   - `OPENAI_API_KEY` in secrets vault (not in `.env`)
   - Model must support vision (e.g., `gpt-4o-mini`, `gpt-4-vision`, `claude-3-5-sonnet`)
   - Test with: `curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"`

3. **TLS Termination**
   - Reverse proxy (nginx, Caddy, cloud LB) in front of `:8000`
   - Redirect HTTP → HTTPS
   - Optional: WAF rules for `/ingest` rate limiting

4. **Monitoring**
   - Redis memory usage (grow as audit trail expands)
   - API response times (LLM latency is primary factor)
   - Exception queue depth: `redis-cli LLEN exception:queue`

5. **Logging**
   - Redirect `docker compose logs` to a log aggregator (ELK, Datadog, CloudWatch)
   - Key patterns: `ERROR`, `CRITICAL`, `LLM_FAILURE`, `REDIS_TIMEOUT`

### Deploy

```bash
# 1. Build images
docker compose build

# 2. Bring up
docker compose up -d

# 3. Verify
docker compose ps
docker compose logs api
curl https://your-domain.com/health
```

### Post-Deploy Checklist

- [ ] Redis is reachable: `redis-cli -h redis ping` → PONG
- [ ] API is healthy: `curl /health` → `{"status":"ok"}`
- [ ] LLM key is valid: logs should NOT show `OPENAI_API_KEY` errors
- [ ] Ingest a test document (PO → Invoice) and confirm it processes
- [ ] Check audit trail: `redis-cli XLEN ap:audit:events` (should be > 0)
- [ ] Check exception state: `redis-cli GET exception:<id>` (should be RECEIVED or RESOLVED)

### Monitoring

#### Redis Health

```bash
# CPU, memory, connected clients
redis-cli INFO server

# Memory used by different key types
redis-cli --scan --pattern 'po:*' | wc -l     # PO cache
redis-cli --scan --pattern 'exception:*' | wc -l  # Exceptions
redis-cli XLEN ap:audit:events                # Audit trail size
```

#### API Logs

```bash
docker compose logs -f api
```

Look for:
- `Starting pipeline for exception` — pipeline invoked
- `Pipeline complete: <id> → RESOLVED` — success
- `OPENAI_API_KEY not set` — missing config
- `Failed to cache PO` — Redis issue

#### Exception Queue

```bash
# List all RECEIVED exceptions (backlog)
redis-cli LRANGE exception:queue 0 -1
redis-cli LLEN exception:queue                # Queue depth
```

### Rollback

```bash
# Stop and revert to previous image
docker compose down
git checkout <previous-commit>
docker compose build
docker compose up -d
```

---

## Scaling

### Horizontal Scaling (Multiple API Instances)

Redis remains the single source of truth. Multiple `api` instances can be deployed behind a load balancer:

```yaml
# docker-compose.yml
services:
  api1:
    build: .
    ports:
      - "8001:8000"
    environment:
      - REDIS_URL=redis://redis:6379/0
      
  api2:
    build: .
    ports:
      - "8002:8000"
    environment:
      - REDIS_URL=redis://redis:6379/0

  # Load balancer (nginx, HAProxy, etc.)
  nginx:
    image: nginx
    ports:
      - "80:80"
    # ... route to api1, api2
```

BackgroundTasks run in-process, so each instance independently processes its assigned exceptions.

### Vertical Scaling (Single Instance)

- Increase Redis memory for larger audit trails and caches
- Increase API instance CPU/memory for more concurrent LLM requests
- Tune `OPENAI_TIMEOUT_SECS` for slower LLM endpoints

### Redis Cluster

For very high volume (>10k exceptions/day):

1. Deploy Redis Cluster (3+ nodes)
2. Point all `REDIS_URL` to the cluster endpoint
3. No application code changes needed (redis-py handles cluster routing)

---

## Troubleshooting

### "PO POO-001 not found" (422)

**Cause:** Invoice references a PO that hasn't been ingested yet.

**Fix:**
1. Ingest the PO via `/ingest` first
2. Wait for success response
3. Then ingest the invoice

POs are cached for 30 days, so re-ingesting is safe.

### "OPENAI_API_KEY not set"

**Cause:** Environment variable not configured.

**Fix:**
```bash
# Check .env
grep OPENAI_API_KEY .env

# Or check running container
docker compose exec api env | grep OPENAI
```

### "Failed to normalize invoice: LLM returned invalid JSON"

**Cause:** LLM extraction did not return valid JSON, or model is not vision-capable for image/PDF.

**Fix:**
1. Check that `OPENAI_MODEL` is vision-capable (e.g., `gpt-4o-mini`, not `gpt-3.5-turbo`)
2. Check LLM timeout: is 30s sufficient? Increase `OPENAI_TIMEOUT_SECS`
3. Check model quota / rate limits
4. Try again with cleaner document image (if image/PDF format)

### Redis memory growing unbounded

**Cause:** Audit trail (`ap:audit:events` Redis Stream) grows with every operation.

**Fix:**
```bash
# Check stream size
redis-cli XLEN ap:audit:events

# Trim stream to last 100k events
redis-cli XTRIM ap:audit:events MAXLEN 100000

# Or automate trimming with a cron job
# redis-cli XTRIM ap:audit:events MAXLEN 100000 APPROXIMATE
```

### Exception stuck in RECEIVED state

**Cause:** Pipeline failed or crashed.

**Fix:**
```bash
# Check exception data
redis-cli GET exception:<id>

# Check recent logs
docker compose logs api | tail -50

# Manually re-trigger pipeline
# (requires code: load exception, call run_pipeline directly)
```

### PO/GRN Cache Expired

**Cause:** 30-day TTL expired; invoice references old PO.

**Fix:**
```bash
# Check remaining TTL
redis-cli TTL po:<po_number>

# Re-ingest the PO
curl -X POST http://localhost:8000/ingest ...
```

---

## Maintenance

### Daily

- Monitor exception queue depth
- Monitor Redis memory usage
- Spot-check a few resolved exceptions in `/exceptions/list`

### Weekly

- Review audit trail for errors: `docker compose logs api | grep ERROR`
- Check LLM API billing / quota
- Trim audit streams if > 500k events

### Monthly

- Backup Redis: `redis-cli BGSAVE`
- Review exception types and patterns (are gates firing as expected?)
- Update dependencies: `pip install --upgrade`

### Quarterly

- Load test: ingest 1000+ exceptions, monitor Redis/API response times
- Review decision gate thresholds (are they still appropriate?)
- Security audit: check for leaked secrets in logs

---

## Performance Tuning

### LLM Latency

- **Timeout:** Increase `OPENAI_TIMEOUT_SECS` if LLM is slow (default 30s)
- **Model:** Choose faster model if available (e.g., `gpt-4o-mini` vs `gpt-4`)
- **Endpoint:** Use regional endpoint closer to deployment

### Redis Latency

- **Network:** Ensure Redis is on low-latency network
- **Persistence:** Disable AOF if not needed for compliance
- **Memory:** Monitor swapping; add more RAM if needed

### Exception Processing

- **Parallel API instances:** Behind load balancer for throughput
- **Background task queue:** FastAPI BackgroundTasks runs sequentially per instance; consider adding a separate task queue if >100 concurrent exceptions needed

---

## Security

### Secrets Management

- Never commit `.env` or API keys to git
- Use platform's secret manager (AWS Secrets Manager, HashiCorp Vault, etc.)
- Rotate `OPENAI_API_KEY` annually

### Network Security

- Redis: No external access (VPC-only)
- API: TLS termination at reverse proxy
- Rate limiting: Apply per IP / per API key if needed

### Audit Trail

- Redis Streams are append-only (immutable)
- Export to central log store for compliance (SOX, audit)
- Example: `redis-cli XRANGE ap:audit:events - + > audit.json`

### Data Retention

- By default, exception records stay in Redis indefinitely
- Implement a cleanup job if data retention policies apply (GDPR, etc.)

---

## Backup & Recovery

### Redis Backup

```bash
# Snapshot
redis-cli BGSAVE

# Copy snapshot
docker cp nocept-redis:/data/dump.rdb ./backups/dump-$(date +%Y%m%d).rdb

# Restore
docker cp ./backups/dump-20260615.rdb nocept-redis:/data/dump.rdb
docker compose restart redis
```

### Application Data Recovery

- Exceptions: Load from Redis keys (`exception:<id>`)
- Audit trail: Replay from Redis Streams (`ap:audit:events`)

### Disaster Recovery

- Weekly: snapshot Redis to off-site storage
- Test restore quarterly
- Document RTO/RPO with stakeholders

---

## Support

- **Logs:** `docker compose logs -f api`
- **Redis CLI:** `redis-cli -h <host> -p <port>`
- **API Docs:** `http://localhost:8000/docs`
- **Health Check:** `curl http://localhost:8000/health`
