"""End-to-end demo: generate a synthetic exception, run the LangGraph agent,
print the resolution memo, and demonstrate knowledge-base search.
"""
from __future__ import annotations

from agent.classifier import classify_exception
from agent.langgraph_agent import run_pipeline
from audit.audit_logger import AuditEvent, AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from clients.tavily_client import TavilyClient
from config.settings import get_settings
from ingestion.erp_simulator import generate_informal_modification_exception
from ingestion.json_ingestor import load_dataset
from knowledge.client import KnowledgeBaseClient
from knowledge.seeder import seed_knowledge_base
from models.exception import ExceptionState, InvoiceException


def main() -> None:
    print("🚀 Starting End-to-End AI Agent Demo...")

    # 1. Setup Infrastructure
    config = get_settings()
    config.configure_logging()
    r = get_redis_connection(config.redis_url)
    store = RedisStateStore_safe(r)
    tavily = TavilyClient(config.tavily_api_key)
    streams = RedisStreamsClient(r, "ap:audit:events")
    audit = AuditLogger(streams)

    # 2. Initialise Knowledge Base (vector search + resolution history)
    print("\n--- 📚 Initialising Knowledge Base…")
    kb = KnowledgeBaseClient.from_config(r, config)

    # 3. Seed historical data from the dataset (upsert-safe, runs every startup)
    print("--- 🌱 Seeding historical data from dataset…")
    bundle = load_dataset()
    counts = seed_knowledge_base(bundle, kb.resolutions, kb.emails, kb.transcripts)
    print(
        f"    Seeded: {counts['resolutions']} resolutions, "
        f"{counts['emails']} emails, {counts['transcripts']} transcripts"
    )

    # 4. Generate a high-interest scenario (Informal Modification)
    # This is the "Golden Path" for the agent to prove its research capabilities
    print("\n--- 📝 Generating complex exception (Informal Modification)...")
    invoice, po, gr = generate_informal_modification_exception("SUP-001")

    print(f"Invoice: {invoice.invoice_number} (${invoice.total_amount})")
    print(f"PO:      {po.po_number} (${po.total_amount})")
    print(f"GR:      {gr.gr_number if gr else 'None'}")
    print(f"Variance: ${round(invoice.total_amount - po.total_amount, 2)}")

    # 5. Run the AI Agent Pipeline
    print("\n--- 🤖 Agent is now processing the exception...")
    print("Steps: Classify → Retrieve Context → Tolerance → History → Comms → Research → Memo\n")

    # 5a. Classify + create + persist the exception (state=RECEIVED)
    class_res = classify_exception(invoice, po, gr, config, store=store)
    exception = InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=gr,
        state=ExceptionState.RECEIVED,
        exception_types=class_res.exception_types,
        line_variances=class_res.line_variances,
        total_variance_usd=class_res.total_variance_usd,
    )
    store.save(exception)
    audit.log(
        AuditEvent(
            exception_id=exception.exception_id,
            event_type="classification",
            details={
                "types": [t.value for t in class_res.exception_types],
                "variance_usd": class_res.total_variance_usd,
            },
        )
    )

    # 5b. Invoke the LangGraph agent
    try:
        resolution = run_pipeline(
            exception.exception_id,
            store,
            audit,
            config,
            tavily,
        )

        print("\n--- ✅ Final Agent Resolution ---")
        print(f"Resolution State: {resolution.final_state.value}")

        print("\nAI Generated Memo Summary:")
        print("========================================================================")
        if resolution.memo and resolution.memo.summary:
            print(resolution.memo.summary)
        else:
            print("(no memo generated — exception was escalated without resolution)")
        print("========================================================================")

        # 6. Ingest the newly resolved case into the knowledge base so future
        #    pipeline runs and human searches can reference it.
        exc_final = store.load(exception.exception_id)
        kb.ingest_resolved_case(exc_final, resolution)
        print(f"\n--- 🗃  Case {exc_final.exception_id} added to knowledge base.")

        # Also surface any related communications already in the KB
        _demo_kb_search(kb, po.po_number)

    except Exception as e:
        print(f"\n❌ Error running pipeline: {e}")
        import traceback
        traceback.print_exc()


def RedisStateStore_safe(r):
    """Local import wrapper to avoid changing the import order above."""
    from state.redis_backend import RedisStateStore
    return RedisStateStore(r)


def _demo_kb_search(kb: KnowledgeBaseClient, po_number: str) -> None:
    """Show a quick demo of KB search capabilities after the pipeline run."""
    print("\n--- 🔍 Knowledge Base Search Demo ---")

    email_hits = kb.search_emails(
        f"price dispute substitution {po_number}",
        top_k=3,
        po_filter=po_number,
    )
    if email_hits:
        print(f"Emails related to {po_number}:")
        for h in email_hits:
            print(f"  [{h.get('score', 0):.2f}] {h.get('subject', '')} — {h.get('sender', '')}")
    else:
        print(f"No emails found for {po_number} (expected — synthetic PO has no dataset emails).")

    transcript_hits = kb.search_transcripts(
        "product substitution price increase",
        top_k=3,
    )
    if transcript_hits:
        print("Transcripts — product substitution / price increase:")
        for h in transcript_hits:
            print(
                f"  [{h.get('score', 0):.2f}] "
                f"{h.get('caller', '')} → {h.get('callee', '')}  ({h.get('date', '')})"
            )


if __name__ == "__main__":
    main()
