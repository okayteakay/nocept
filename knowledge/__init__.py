"""knowledge — Redis-backed knowledge base for AP exception resolution.

Three stores:
  - ResolutionHistoryStore: structured Hash+SortedSet store for finalized cases
  - EmailVectorStore: HNSW vector index for email communications
  - TranscriptVectorStore: HNSW vector index for phone call transcripts

Entry point: KnowledgeBaseClient (wraps all three, shared Embedder).
Startup:     seed_knowledge_base() (upserts all dataset records on boot).
"""
from knowledge.client import KnowledgeBaseClient
from knowledge.email_store import EmailVectorStore
from knowledge.embedder import Embedder
from knowledge.resolution_store import ResolutionHistoryStore
from knowledge.seeder import seed_knowledge_base
from knowledge.transcript_store import TranscriptVectorStore

__all__ = [
    "KnowledgeBaseClient",
    "EmailVectorStore",
    "Embedder",
    "ResolutionHistoryStore",
    "TranscriptVectorStore",
    "seed_knowledge_base",
]
