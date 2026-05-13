# Quick Reference: Error Handling Improvements

## What Changed

### 🔴 **Critical: agent/pipeline.py** (Main Orchestrator)
- **Before:** Any error → crash
- **After:** 25+ try/except blocks → escalate to human on any failure
- **Key:** Top-level handler returns minimal escalation record even if everything fails

### 🟠 **High: agent/classifier.py** (Classification Engine)  
- **Before:** Zero error handling, crashes on bad data
- **After:** 11 try/except blocks protecting all gates
- **Key:** Returns empty classification on failure (triggers escalation)

### 🟠 **High: agent/comms_checker.py** (LLM Integration)
- **Before:** Hangs on LLM timeout, no fallback
- **After:** 30-second timeout, automatic keyword fallback
- **Key:** LLM failure → keyword analysis (60% confidence max)

### 🟠 **High: agent/researcher.py** (Tavily Integration)
- **Before:** Search failures crash pipeline
- **After:** Per-query error handling, timeout-aware
- **Key:** Search failure → escalate with "no research evidence"

### 🟡 **Medium: agent/context_retriever.py** (Redis Queries)
- **Before:** Zero logging, crashes on Redis error
- **After:** 6 try/except blocks, fail-safe to empty context
- **Key:** Redis down → continue with empty supplier history

### 🟢 **Config: config/settings.py + .env**
- **Added:** OPENAI_TIMEOUT_SECS, TAVILY_TIMEOUT_SECS, REDIS_TIMEOUT_SECS
- **Defaults:** 30s, 30s, 5s (all configurable)

---

## What's Now Protected

### External API Calls
| System | Timeout | Fallback |
|--------|---------|----------|
| **OpenAI LLM** | 30s (configurable) | Keyword analysis (40-50% confidence) |
| **Tavily Search** | 30s (configurable) | Empty findings → escalate |
| **Redis** | 5s (configurable) | Empty context → continue |

### Data Validation
| Check | Before | After |
|-------|--------|-------|
| **Division by zero** | ❌ Crash | ✅ Guarded, logs warning |
| **Missing attributes** | ❌ Crash | ✅ Safe access with fallback |
| **Empty lists** | ❌ Crash | ✅ Handled gracefully |
| **JSON parsing** | ❌ Crash | ✅ Try/except, fallback |
| **Null fields** | ❌ Crash | ✅ None-checks, defaults |

### State Transitions
| Scenario | Before | After |
|----------|--------|-------|
| **Transition fails** | ❌ Exception stalled | ✅ Escalate to human |
| **Memo gen fails** | ❌ Crash | ✅ Continue with null memo |
| **Audit log fails** | ❌ Crash | ✅ Log warning, continue |
| **Redis save fails** | ❌ Lost data | ✅ Logged, escalated |

---

## Testing It

### Quick Smoke Test (No Docker)
```bash
# Check syntax
python3 -m py_compile agent/*.py config/*.py

# Expected: No output (syntax valid)
```

### Integration Test (With Docker)
```bash
# 1. Stop Redis
docker compose stop redis

# 2. Send invoice webhook
curl -X POST http://localhost:8002/webhook/invoice ...

# 3. Expected: Exception created, escalated (no crash)
# 4. Check logs: docker compose logs -f worker
# 5. Verify: "Failed to retrieve supplier context"
```

---

## Logging Output Examples

### Success Path
```
2026-05-13 10:15:32 INFO context_retriever: Retrieved 42 total exceptions for supplier SUP-001
2026-05-13 10:15:32 DEBUG context_retriever: Filtered to 8 recent exceptions within 180 days
2026-05-13 10:15:33 INFO researcher: Tavily query 'Widget A price increase 2026' returned 3 results
2026-05-13 10:15:34 INFO pipeline: Exception EXC-001 resolved with confidence 0.90
```

### Degradation Path (LLM Timeout)
```
2026-05-13 10:15:32 WARNING comms_checker: LLM request timed out for email email-001 (>30s)
2026-05-13 10:15:32 INFO comms_checker: Keyword fallback for email email-001: 2/4 families hit → confidence 0.55
2026-05-13 10:15:33 INFO pipeline: Gate communications did not approve (confidence 0.55 < 0.75)
```

### Escalation Path (Redis Down)
```
2026-05-13 10:15:32 ERROR context_retriever: Failed to retrieve exceptions for supplier SUP-001: Connection refused
2026-05-13 10:15:32 WARNING pipeline: Failed to retrieve supplier context: Connection refused
2026-05-13 10:15:32 INFO pipeline: Exception EXC-001 escalated to human (ESCALATED state)
```

---

## Configuration Tuning

### For High Latency Networks
```bash
# .env
OPENAI_TIMEOUT_SECS=60
TAVILY_TIMEOUT_SECS=45
REDIS_TIMEOUT_SECS=10
```

### For Strict SLA
```bash
# .env
OPENAI_TIMEOUT_SECS=10
TAVILY_TIMEOUT_SECS=15
REDIS_TIMEOUT_SECS=2
```

### For Maximum Visibility
```bash
# .env
LOG_LEVEL=DEBUG  # See every step
```

---

## Rollout Checklist

- [ ] Review ERROR_HANDLING_IMPROVEMENTS.md
- [ ] Update .env with appropriate timeouts
- [ ] Set LOG_LEVEL=INFO (default)
- [ ] Test with Redis down: `docker compose stop redis`
- [ ] Test with LLM disabled: Comment OPENAI_API_KEY
- [ ] Monitor logs for 24 hours post-deploy
- [ ] Alert if escalation rate > 25%
- [ ] Document any new patterns in runbook

---

## Files Modified

```
✅ agent/context_retriever.py      — +40 lines of error handling
✅ agent/classifier.py              — +70 lines of error handling  
✅ agent/comms_checker.py           — +35 lines of error handling
✅ agent/researcher.py              — +45 lines of error handling
✅ agent/pipeline.py                — +100 lines of error handling
✅ config/settings.py               — +10 timeout configs
✅ .env                             — +3 timeout env vars

📄 ERROR_HANDLING_IMPROVEMENTS.md   — Detailed documentation
📄 IMPROVEMENTS_QUICK_REF.md        — This file
```

---

## Support

**Issue:** Pipeline crashes on external API timeout  
**Fix:** Already handled (30s timeout, automatic fallback)

**Issue:** Redis connection errors in logs  
**Fix:** Expected; system continues with empty context (graceful degradation)

**Issue:** Low confidence in LLM fallback  
**Fix:** Expected (keyword analysis ~50% confidence); human review provides final decision

**Issue:** Escalation rate too high  
**Fix:** Review timeout settings; may indicate slow APIs (increase timeout)

