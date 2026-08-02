# DESIGN NOTE: this module DOES its own file I/O, unlike the rest of
# src/category/ -- it caches embeddings to disk for the same reason
# src/extractors/gemini.py caches model responses: avoiding repeat API cost
# on every re-run is a practical necessity, not part of the pure retrieval
# core (corpus.py / filter.py / classifier.py stay pure).
"""Gemini embeddings for the category corpus and for query text, with a
disk cache. THE MODEL ID MUST STAY IN THE CACHE KEY: gemini-embedding-001
and gemini-embedding-2 have INCOMPATIBLE embedding spaces, so a cache
collision here would silently produce meaningless similarity scores.
"""

import hashlib
import json
import time

import numpy as np
from google import genai
from google.genai import errors

from config import settings

DIMENSIONALITY = 768
MAX_BATCH_SIZE = 100  # the API's own BatchEmbedContentsRequest limit
# The free tier's embed_content quota is 100 requests/MINUTE, and each text
# in a batch counts as one request -- a single 100-item batch can exhaust an
# entire minute's quota by itself. Retries need to be able to span a full
# quota window (and, for the 155-document corpus, more than one), not just
# back off within a few seconds.
MAX_RATE_LIMIT_RETRIES = 8
INITIAL_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 65.0
# Same retryable set as src/extractors/gemini.py: 429 = rate limited,
# 500/503 = transient server-side overload.
RETRYABLE_STATUS_CODES = {429, 500, 503}

_CACHE_PATH = settings.REFERENCE_DIR / "category_embeddings.json"


def _cache_key(text: str, model_id: str, dimensionality: int) -> str:
    digest = hashlib.sha256()
    digest.update(text.encode("utf-8"))
    digest.update(model_id.encode("utf-8"))
    digest.update(str(dimensionality).encode("utf-8"))
    return digest.hexdigest()


def _load_cache() -> dict[str, list[float]]:
    if not _CACHE_PATH.exists():
        return {}
    return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))


def _save_cache(cache: dict[str, list[float]]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _normalise(vector: list[float]) -> list[float]:
    v = np.array(vector, dtype=float)
    norm = np.linalg.norm(v)
    if norm == 0:
        return vector
    return (v / norm).tolist()


class GeminiEmbedder:
    """Embeds document/query text via gemini-embedding-001, cached to disk."""

    def __init__(self, model_id: str | None = None):
        self.model_id = model_id or settings.EMBEDDING_MODEL
        self._client = genai.Client(api_key=settings.GOOGLE_API_KEY)

    def embed_documents(self, texts: dict[str, str]) -> dict[str, list[float]]:
        """code -> unit-normalised embedding vector, RETRIEVAL_DOCUMENT task type."""
        return self._embed_many(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed_many({"_query": text}, task_type="RETRIEVAL_QUERY")["_query"]

    def _embed_many(self, texts: dict[str, str], task_type: str) -> dict[str, list[float]]:
        cache = _load_cache()
        results: dict[str, list[float]] = {}
        to_fetch: dict[str, str] = {}
        for key, text in texts.items():
            cache_key = _cache_key(text, self.model_id, DIMENSIONALITY)
            if cache_key in cache:
                results[key] = cache[cache_key]
            else:
                to_fetch[key] = text

        if to_fetch:
            keys = list(to_fetch.keys())
            vectors = self._call_model([to_fetch[k] for k in keys], task_type)
            for key, vector in zip(keys, vectors, strict=True):
                normalised = _normalise(vector)
                results[key] = normalised
                cache[_cache_key(to_fetch[key], self.model_id, DIMENSIONALITY)] = normalised
            _save_cache(cache)

        return results

    def _call_model(self, contents: list[str], task_type: str) -> list[list[float]]:
        """Embed all of `contents`, chunked to the API's batch limit."""
        vectors = []
        for start in range(0, len(contents), MAX_BATCH_SIZE):
            vectors.extend(self._call_model_batch(contents[start : start + MAX_BATCH_SIZE], task_type))
        return vectors

    def _call_model_batch(self, contents: list[str], task_type: str) -> list[list[float]]:
        """Call the model once for one batch, retrying with exponential backoff
        on transient errors."""
        delay = INITIAL_BACKOFF_SECONDS
        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            try:
                response = self._client.models.embed_content(
                    model=self.model_id,
                    contents=contents,
                    config={"output_dimensionality": DIMENSIONALITY, "task_type": task_type},
                )
                return [e.values for e in response.embeddings]
            except errors.APIError as exc:
                if exc.code in RETRYABLE_STATUS_CODES and attempt < MAX_RATE_LIMIT_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_BACKOFF_SECONDS)
                    continue
                raise
        raise RuntimeError("unreachable")
