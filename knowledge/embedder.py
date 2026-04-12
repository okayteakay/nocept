"""knowledge/embedder.py

Thin wrapper around sentence-transformers for producing float32 vectors.

The model is loaded lazily on first use so that importing this module never
triggers a heavyweight download.  A single process-level singleton is kept so
the model is only loaded once regardless of how many store instances exist.
"""
from __future__ import annotations

import logging
import struct
from functools import lru_cache
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Process-level singleton – avoids reloading the model on every call.
_model_cache: dict[str, object] = {}


@lru_cache(maxsize=4)
def _get_model(model_name: str):
    """Load (and cache) a SentenceTransformer model by name."""
    from sentence_transformers import SentenceTransformer  # type: ignore

    logger.info("Loading embedding model '%s' (first-use download if not cached)…", model_name)
    model = SentenceTransformer(model_name)
    logger.info("Embedding model '%s' ready.", model_name)
    return model


class Embedder:
    """Produces L2-normalised float32 embedding vectors from text.

    Args:
        model_name: HuggingFace / sentence-transformers model identifier.
                    Default is ``all-MiniLM-L6-v2`` (384-d, ~90 MB).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed(self, text: str) -> bytes:
        """Return the embedding of *text* as a Redis-ready BLOB (float32 LE bytes).

        The vector is L2-normalised so cosine similarity == dot product, which
        lets us use Redis's FLAT/HNSW indexes with ``DISTANCE_METRIC COSINE``.
        """
        vec = self._encode([text])[0]
        return _to_blob(vec)

    def embed_batch(self, texts: Sequence[str]) -> list[bytes]:
        """Embed a batch of texts.  More efficient than calling embed() in a loop."""
        vecs = self._encode(list(texts))
        return [_to_blob(v) for v in vecs]

    @property
    def dimensions(self) -> int:
        """Return the output dimensionality of the loaded model."""
        model = _get_model(self._model_name)
        return model.get_sentence_embedding_dimension()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _encode(self, texts: list[str]) -> list[np.ndarray]:
        model = _get_model(self._model_name)
        # normalize_embeddings=True → L2 unit vectors (cosine = dot product)
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [np.array(v, dtype=np.float32) for v in vecs]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_blob(vec: np.ndarray) -> bytes:
    """Pack a float32 numpy vector into little-endian bytes for Redis."""
    return vec.astype(np.float32).tobytes()
