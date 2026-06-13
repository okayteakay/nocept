# Operations Guide

Running, deploying, monitoring, and troubleshooting the Invoice Exception
Resolution system in production.

---

## Local Development (5 minutes)

### Prerequisites
- Python 3.11+
- Docker + Docker Compose
- API keys: `TAVILY_API_KEY` (Step 5), `OPENAI_API_KEY` (Step 4)

### Start everything
```bash
cp .env.example .env       # fill in API keys
docker compose up -d
```

This brings up six services:

| Service    | Port | Purpose |
|------------|------|---------|
| `redis`    | 6379, 8001 (Insight) | State store, audit, KB, Celery broker |
| `api`      | 8000 | FastAPI dashboard + auth + KB + analytics |
| `webhook`  | 8002 | HMAC-signed SAP event receiver |
| `worker`   | — | Celery worker running the LangGraph pipeline |
| `dashboard`| 8502 | Streamlit UI (login: `admin` / `admin123`) |
| `flower`   | 5555 | Celery task monitor |

### Verify it works
```bash
# Submit a synthetic exception through the dashboard "Demo Trigger" tab
# OR run the end-to-end demo
uv run python run_demo.py
```

---

## Production Deployment

### Stack
- Python 3.11 (slim Docker base)
- Redis Stack 7.0+ (state, audit, KB, broker)
- 6 Docker services orchestrated by Compose (or Kubernetes — see `k8s/` if present)

### Pre-flight
1. **Provision Redis** with 10GB+ memory, persistence (RDB + AOF), and daily backups
2. **TLS termination** at a reverse proxy (Caddy / nginx / cloud LB) for the API
3. **Secrets in vault** — never commit `.env`; inject via your platform's secret manager
4. **Slack workspace** with an incoming webhook URL for escalation alerts
5. **SMTP credentials** for email notifications
6. **JWT secret** — generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`
7. **SAP S/4HANA** with outbound webhooks to the webhook service (HMAC shared secret configured)

### Deploy
```bash
# Build images
docker compose build

# Bring up (one command)
docker compose up -d

# Verify
docker compose ps
docker compose logs -f worker       # should show "celery@... ready"
curl http://localhost:8000/health   # returns {"status":"ok"}
open http://localhost:8001           # RedisInsight
open http://localhost:5555           # Flower (Celery tasks)
```

### Post-deploy
1. Send a test PO + Invoice + GRN sequence via `curl` to the webhook ports
2. Watch the exception land in the dashboard within 10–30s
3. Confirm a Slack alert fires on escalation
4. Confirm an audit event is written to Redis Streams (`XLEN ap:audit:events`)

### Rollback
```bash
docker compose down            # stop everything
docker compose up -d           # restart (data persisted in redis_data volume)
# To roll back a code change:
git checkout <previous-tag>
docker compose build && docker compose up -d
```

The previous exception state and audit trail are in the `redis_data` volume,
so rollback never loses data.

---

## Monitoring

### Health endpoints
- API: `GET /health` → 200 OK
- Webhook: `GET /health` → 200 OK
- Redis: `docker compose exec redis redis-cli ping` → PONG
- Worker: check Flower at `:5555` (task throughput, queue depth, error rate)

### Key metrics to watch
- **API p95 latency** — target <500ms; alert if >1000ms for 5 min
- **Worker queue depth** (Flower) — alert if >100 for 5 min
- **Exception processing time** — target <30s end-to-end; alert if >120s
- **Auto-resolution rate** — target 60–80%; alert if drops below 50%
- **Audit event growth** — should be roughly linear with invoice volume

### Logs
```bash
docker compose logs -f api        # API access + error logs
docker compose logs -f worker     # Celery task execution
docker compose logs -f webhook    # SAP webhook receiver
docker compose logs -f dashboard  # Streamlit UI
docker compose logs -f redis      # Redis slowlog, evictions
```

### Backup strategy
- **Redis RDB + AOF** for state and audit (snapshot every 60s if 1+ write, per docker-compose)
- **Daily backup of `redis_data` volume** to S3 / equivalent
- **Audit trail is append-only** — Redis Streams survives restarts
- **Disaster recovery**: restore from latest snapshot, replay any in-flight exceptions via `POST /webhook/invoice` re-trigger

---

## Troubleshooting

### "Worker not picking up tasks"
Check Celery broker connectivity:
```bash
docker compose exec worker celery -A worker.celery_app inspect ping
```
If workers respond → check the queue: `celery -A worker.celery_app inspect active`. If not, check Redis: `docker compose exec redis redis-cli ping`. If Redis is up, check `CELERY_BROKER_URL` matches in `.env`.

### "LLM is unavailable" warnings in logs
- Verify `OPENAI_API_KEY` is set
- For nanogpt, verify `OPENAI_BASE_URL=https://nano-gpt.com/api/v1`
- Test with a direct call: `curl -H "Authorization: Bearer $OPENAI_API_KEY" $OPENAI_BASE_URL/models`

### "Tavily search timed out"
- Verify `TAVILY_API_KEY` is set
- Increase `TAVILY_TIMEOUT_SECS` in `.env` (default 30s)
- Tavily rate limits: 1000 calls/month on free tier

### "Duplicate invoice false positives"
Check `agent/classifier.py::check_duplicate` — it uses
`(supplier_id, invoice_number, total_amount)` as the fingerprint. If a supplier
issues a *legitimate* repeat invoice (same number + same amount), the system
will flag it as a duplicate. Solution: per-supplier override rules
(not yet implemented; track in v1.1).

### "Webhook returns 401"
HMAC signature verification failed. The header `X-SAP-Signature` must be
the hex SHA-256 of the raw request body using `SAP_WEBHOOK_SECRET`. The
webhook handler logs the actual vs expected signatures in debug mode.

### State machine refuses a transition
`InvalidTransitionError` — the agent tried to move from one state to
another via an illegal jump. Check `state/machine.py::VALID_TRANSITIONS`
for the allowed edges. Common cause: human approval endpoint called on
an exception in a non-terminal-but-not-ESCALATED state.

---

## SLA Targets (production)

| Metric | Target | PagerDuty alert if |
|--------|--------|--------------------|
| API uptime | 99.9% | <99.5% over 1h |
| Webhook receiver uptime | 99.9% | <99.5% over 1h |
| Exception resolution time (p95) | <60s | >120s over 15 min |
| Auto-resolution rate | ≥60% | <50% over 1h |
| Audit completeness | 100% | Any missing event |

---

## On-Call Runbook

1. **P1: Webhook down**
   - Check `docker compose logs webhook`
   - Verify SAP S/4HANA can reach the webhook port
   - Restart: `docker compose restart webhook`
   - Escalate to IT if port/network issue

2. **P2: Worker backlog growing**
   - Check Flower: `:5555`
   - Scale workers: `docker compose up -d --scale worker=4`
   - If LLM/Tavily rate-limited, increase timeouts in `.env`

3. **P3: Auto-resolution rate dropped**
   - Check recent invoice/PO data for pattern changes
   - Check `PRICE_TOLERANCE_PCT` env var (default 0.01)
   - Review recent audit trail for new failure modes

4. **P4: User reports bad auto-approval**
   - Pull the exception_id from the user's report
   - `XREAD STREAMS ap:audit:events ...` to get the audit trail
   - Identify which gate fired and why
   - If a gate is systematically wrong, file a v1.1 bug

---

## See also

- [README.md](README.md) — what the system is
- [ARCHITECTURE.md](ARCHITECTURE.md) — system design
- [API.md](API.md) — endpoint reference
- [USER_TRAINING_MATERIALS.md](USER_TRAINING_MATERIALS.md) — training materials for the AP team
- [docs/archive/](docs/archive/) — historical build & deployment notes (kept for reference)
