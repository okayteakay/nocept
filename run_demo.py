from config.settings import get_settings
from clients.redis_client import get_redis_connection
from clients.tavily_client import TavilyClient
from state.redis_backend import RedisStateStore
from audit.audit_logger import AuditLogger, RedisStreamsClient
from agent.pipeline import run_pipeline
from ingestion.erp_simulator import generate_informal_modification_exception, generate_batch

def main():
    print("🚀 Starting End-to-End AI Agent Demo...")
    
    # 1. Setup Infrastructure
    config = get_settings()
    r = get_redis_connection(config.redis_url)
    store = RedisStateStore(r)
    tavily = TavilyClient(config.tavily_api_key)
    streams = RedisStreamsClient(r, "ap:audit:events")
    audit = AuditLogger(streams)

    # 2. Generate a high-interest scenario (Informal Modification)
    # This is the "Golden Path" for the agent to prove its research capabilities
    print("\n--- 📝 Generating complex exception (Informal Modification)...")
    invoice, po, gr = generate_informal_modification_exception("SUP-001")
    
    print(f"Invoice: {invoice.invoice_number} (${invoice.total_amount})")
    print(f"PO:      {po.po_number} (${po.total_amount})")
    print(f"GR:      {gr.gr_number if gr else 'None'}")
    print(f"Variance: ${round(invoice.total_amount - po.total_amount, 2)}")

    # 3. Run the AI Agent Pipeline
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
        
    except Exception as e:
        print(f"\n❌ Error running pipeline: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
