from config.settings import get_settings
from clients.redis_client import get_redis_connection
from clients.tavily_client import TavilyClient
from state.redis_backend import RedisStateStore
from audit.audit_logger import AuditLogger, RedisStreamsClient
from agent.pipeline import run_pipeline
from ingestion.erp_simulator import generate_informal_modification_exception, generate_batch
from ingestion.json_ingestor import load_dataset
from knowledge.client import KnowledgeBaseClient
from knowledge.seeder import seed_knowledge_base


def main():
    print("🚀 Starting End-to-End AI Agent Demo...")

    # 1. Setup Infrastructure
    config = get_settings()
    r = get_redis_connection(config.redis_url)
    store = RedisStateStore(r)
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
    print("Steps: Classify -> Retrieve Context -> Research -> Apply Rules -> Generate Memo\n")

    try:
        result = run_pipeline(invoice, po, gr, store, tavily, audit, config)

        print("\n--- ✅ Final Agent Resolution ---")
        print(f"Resolution State: {result.resolution.final_state.value}")
        print("\nAI Generated Memo Summary:")
        print("========================================================================")
        print(result.resolution.memo.summary)
        print("========================================================================")

        # 6. Ingest the newly resolved case into the knowledge base so future
        #    pipeline runs and human searches can reference it.
        kb.ingest_resolved_case(result.exception, result.resolution)
        print(f"\n--- 🗃  Case {result.exception.exception_id} added to knowledge base.")

        # Also surface any related communications already in the KB
        _demo_kb_search(kb, po.po_number)

    except Exception as e:
        print(f"\n❌ Error running pipeline: {e}")
        import traceback
        traceback.print_exc()


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
