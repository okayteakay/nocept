# Production Deployment Guide

**System:** Invoice Exception Resolution (Receiptfinder)  
**Date:** May 13, 2026  
**Audience:** DevOps/IT Team  

---

## Quick Start (5 minutes)

### Prerequisites
- Python 3.11+
- Redis 7.0+
- Docker (optional)
- Environment variables configured

### Deploy API

```bash
# 1. Clone repo
git clone <repo-url>
cd receiptfinder

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
export REDIS_HOST=redis.company.com
export REDIS_PORT=6379
export API_PORT=8000
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
export SMTP_HOST=mail.company.com

# 4. Start API
python -m uvicorn orchestrate.api:app --host 0.0.0.0 --port 8000 --workers 4

# 5. Verify
curl http://localhost:8000/health
```

### Deploy Dashboard

```bash
# Terminal 2
cd dashboard
streamlit run app.py --server.port=8501 --server.address=0.0.0.0
```

---

## Docker Deployment (Recommended for Production)

### Build Images

```dockerfile
# Dockerfile (API)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "orchestrate.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

```dockerfile
# Dockerfile.dashboard
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

### Build and Push

```bash
# API
docker build -t company-registry/ap-api:latest .
docker push company-registry/ap-api:latest

# Dashboard
docker build -f Dockerfile.dashboard -t company-registry/ap-dashboard:latest .
docker push company-registry/ap-dashboard:latest
```

---

## Kubernetes Deployment (Production)

### API Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ap-api
  namespace: ap
spec:
  replicas: 3
  selector:
    matchLabels:
      app: ap-api
  template:
    metadata:
      labels:
        app: ap-api
    spec:
      containers:
      - name: api
        image: company-registry/ap-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: REDIS_HOST
          valueFrom:
            configMapKeyRef:
              name: ap-config
              key: redis-host
        - name: SLACK_WEBHOOK_URL
          valueFrom:
            secretKeyRef:
              name: ap-secrets
              key: slack-webhook
        - name: SMTP_HOST
          valueFrom:
            configMapKeyRef:
              name: ap-config
              key: smtp-host
        - name: LOG_LEVEL
          value: "INFO"
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: ap-api
  namespace: ap
spec:
  selector:
    app: ap-api
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
  type: LoadBalancer
```

### Dashboard Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ap-dashboard
  namespace: ap
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ap-dashboard
  template:
    metadata:
      labels:
        app: ap-dashboard
    spec:
      containers:
      - name: dashboard
        image: company-registry/ap-dashboard:latest
        ports:
        - containerPort: 8501
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /_stcore/health
            port: 8501
          initialDelaySeconds: 30
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: ap-dashboard
  namespace: ap
spec:
  selector:
    app: ap-dashboard
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8501
  type: LoadBalancer
```

### Deploy

```bash
kubectl apply -f deployment-api.yaml
kubectl apply -f deployment-dashboard.yaml
kubectl get pods -n ap
```

---

## Configuration Management

### ConfigMap (Non-sensitive)

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ap-config
  namespace: ap
data:
  redis-host: "redis-primary.infra.svc.cluster.local"
  redis-port: "6379"
  api-port: "8000"
  log-level: "INFO"
  smtp-host: "mail.company.com"
  smtp-port: "587"
  slack-channel: "ap-exceptions"
```

### Secrets (Sensitive)

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: ap-secrets
  namespace: ap
type: Opaque
stringData:
  redis-password: "<secure-password>"
  slack-webhook-url: "https://hooks.slack.com/services/..."
  smtp-user: "ap-notifications@company.com"
  smtp-password: "<secure-password>"
  api-key: "<production-api-key>"
```

---

## Health Check Configuration

### API Health Endpoint

```python
# GET /health
# Response:
{
  "status": "healthy",
  "version": "1.0.0",
  "dependencies": {
    "redis": "connected",
    "slack": "configured",
    "smtp": "configured"
  },
  "timestamp": "2026-05-13T10:00:00Z"
}
```

### Monitoring Health

```bash
# Check API
curl http://api.company.com:8000/health

# Check Dashboard
curl http://dashboard.company.com:8501/_stcore/health

# Check Redis
redis-cli -h redis.company.com ping
# PONG

# Check logs
kubectl logs -f deployment/ap-api -n ap
```

---

## SAP Webhook Configuration

### Register Webhook in SAP S/4HANA

```
Customizing > Data Integration > Webhooks > Create

URL: https://api.company.com/webhooks/sap
Method: POST
Authentication: Header
Secret: <SAP_WEBHOOK_SECRET>
Event Types: PO_CREATED, INVOICE_RECEIVED, GRN_RECEIVED
Active: Yes
```

### Test Webhook

```bash
# Get webhook test script from config
curl -X POST https://api.company.com/webhooks/sap \
  -H "Content-Type: application/json" \
  -H "X-SAP-Signature: $(openssl dgst -sha256 -hmac $SAP_WEBHOOK_SECRET -r | awk '{print $1}')" \
  -d '{
    "event": "po_created",
    "po_number": "TEST001",
    "vendor": "0000100000",
    "line_items": [{"quantity": 100, "unit_price": 50.00}],
    "document_date": "2026-05-13"
  }'

# Verify in logs
kubectl logs -f deployment/ap-api -n ap | grep "po_created"
```

---

## Database Backup Strategy

### Redis Persistence

```bash
# Verify RDB snapshots
redis-cli -h redis.company.com CONFIG GET save
# save 900 1 (snapshot every 15 min if 1+ change)

# Verify AOF persistence
redis-cli -h redis.company.com CONFIG GET appendonly
# appendonly yes (append-only file for durability)

# Manual backup
redis-cli -h redis.company.com BGSAVE
```

### Daily Backup Script

```bash
#!/bin/bash
# backup-redis.sh

REDIS_HOST=${REDIS_HOST:-redis.company.com}
BACKUP_DIR=/backups/redis
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p $BACKUP_DIR

# Connect and save
redis-cli -h $REDIS_HOST BGSAVE

# Wait for save to complete
sleep 30

# Copy dump file
cp /var/lib/redis/dump.rdb $BACKUP_DIR/dump-$TIMESTAMP.rdb

# Rotate old backups (keep 30 days)
find $BACKUP_DIR -mtime +30 -delete

echo "Redis backup completed: $BACKUP_DIR/dump-$TIMESTAMP.rdb"
```

### Restore from Backup

```bash
# 1. Stop API
kubectl scale deployment ap-api --replicas=0 -n ap

# 2. Restore Redis
redis-cli -h redis.company.com shutdown
cp /backups/redis/dump-20260513-100000.rdb /var/lib/redis/dump.rdb
redis-server /etc/redis/redis.conf

# 3. Verify
redis-cli -h redis.company.com DBSIZE

# 4. Start API
kubectl scale deployment ap-api --replicas=3 -n ap
```

---

## Monitoring & Logging

### Prometheus Metrics

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'ap-api'
    static_configs:
      - targets: ['api.company.com:8000']
    metrics_path: '/metrics'
```

### Alert Rules

```yaml
# alerts.yml
groups:
- name: ap-api
  rules:
  - alert: HighErrorRate
    expr: rate(requests_total{status=~"5.."}[5m]) > 0.01
    for: 5m
    annotations:
      summary: "High error rate on AP API"

  - alert: SlowRequests
    expr: histogram_quantile(0.95, request_duration_seconds) > 1
    for: 5m
    annotations:
      summary: "P95 latency > 1s on AP API"

  - alert: RedisDown
    expr: redis_up == 0
    for: 1m
    annotations:
      summary: "Redis connection lost"
```

### Logging

```bash
# View API logs
kubectl logs -f deployment/ap-api -n ap

# View Dashboard logs
kubectl logs -f deployment/ap-dashboard -n ap

# Search for errors
kubectl logs deployment/ap-api -n ap | grep ERROR

# Export logs for analysis
kubectl logs deployment/ap-api -n ap --timestamps=true > api-logs.txt
```

---

## Troubleshooting

### API Not Starting

```bash
# Check logs
docker logs ap-api

# Check environment
docker exec ap-api env | grep REDIS

# Test Redis connection
docker exec ap-api python -c "import redis; r = redis.Redis(host='redis'); print(r.ping())"
```

### Dashboard Connection Issues

```bash
# Verify API is running
curl http://api.company.com:8000/health

# Check network connectivity
ping api.company.com
curl -v http://api.company.com:8000/

# Check dashboard logs
kubectl logs -f deployment/ap-dashboard -n ap
```

### Slack Notifications Not Working

```bash
# Verify webhook URL
echo $SLACK_WEBHOOK_URL

# Test webhook
curl -X POST $SLACK_WEBHOOK_URL \
  -H 'Content-Type: application/json' \
  -d '{"text":"Test message"}'

# Check API logs for errors
kubectl logs deployment/ap-api -n ap | grep slack
```

### High Memory Usage

```bash
# Check memory usage
kubectl top pods -n ap

# Increase memory limits if needed
kubectl set resources deployment ap-api -n ap \
  --limits=memory=1Gi,cpu=1000m \
  --requests=memory=512Mi,cpu=500m
```

---

## Rollback Procedure

### If Critical Issue Found

```bash
# 1. Identify previous working version
kubectl rollout history deployment/ap-api -n ap

# 2. Rollback to previous version
kubectl rollout undo deployment/ap-api -n ap

# 3. Verify
kubectl get pods -n ap
curl http://api.company.com:8000/health

# 4. Investigate issue
kubectl logs deployment/ap-api -n ap --timestamps=true > failure-logs.txt
```

---

## Performance Tuning

### API Workers

```bash
# Determine optimal workers = (2 * CPU_COUNT) + 1
# For 4 CPU instance: 2*4+1 = 9 workers
python -m uvicorn orchestrate.api:app --workers 9
```

### Redis Optimization

```bash
# Increase max memory if needed
redis-cli CONFIG SET maxmemory 8gb
redis-cli CONFIG SET maxmemory-policy allkeys-lru

# Optimize for throughput
redis-cli CONFIG SET tcp-keepalive 300
```

### Streamlit Performance

```bash
# dashboard/config.toml
[client]
maxMessageSize = 200

[server]
maxUploadSize = 200
enableXsrfProtection = true
runOnSave = false
```

---

## Maintenance Schedule

- **Daily:** Check health metrics, review error logs
- **Weekly:** Review performance trends, update security patches
- **Monthly:** Full backup verification, capacity planning
- **Quarterly:** Load testing, disaster recovery drill

---

**Last Updated:** May 13, 2026  
**Status:** Ready for Production Deployment
