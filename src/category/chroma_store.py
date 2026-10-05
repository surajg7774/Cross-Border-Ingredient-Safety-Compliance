# DESIGN RULE: pure functions over documents/embeddings passed in as
# arguments -- no file reads, no printing, same rules as the rest of
# src/category/. build_chroma_index does construct a chromadb client --
# either an in-memory EphemeralClient (persist_path=None, the ablation
# harness's own path, unchanged since F-11) or a PersistentClient writing
# to persist_path (production, see src/graph/pipeline.py) -- but never
# touches the network either way; the corpus/embeddings it indexes are
# ALWAYS supplied by the caller, already computed.
"""ChromaDB retrieval over the category corpus -- a vector-STORE ablation,
not an embedding-model one. The original project design specified ChromaDB
as the vector store; scoping to EU-only reduced the corpus to 155 category
documents, at which exhaustive cosine similarity in numpy (classifier.py's
cosine_similarity/embedding_scores) is exact and instant. F-11
(docs/findings.md) measured this once: identical ranking to the "embedding"
path, byte-for-byte, at additional cost, and was NOT adopted for accuracy --
there is none to gain. It is adopted here, later, for STORE PROPERTIES
(persistence, metadata filtering) instead -- see settings.RETRIEVAL_STORE
and src/graph/pipeline.py. Framing this as a retrieval improvement anywhere
would misrepresent what F-11 measured; don't.

EXPECTED: identical ranking to the "embedding" path, since both use the
same vectors and the same cosine metric over the same documents. Any
difference is either HNSW approximation (Chroma builds an approximate
index; exhaustive numpy search does not) or a scoring-convention bug. A
large divergence means the latter -- check the distance-to-similarity
conversion first.

STALENESS (persist_path mode only): a persisted index is only as
trustworthy as its last build. Every persistent build stores a corpus_hash
(codes + document texts + model id + dimensionality) as collection
metadata; every subsequent call recomputes that same hash from what it was
GIVEN and rebuilds from scratch whenever it doesn't match -- corpus text
edited, embedding model swapped, or the collection has never been built.
A rebuild costs zero API calls (the embeddings are already-computed,
passed in by the caller) and well under a second at 155 documents (F-11:
~0.32s to build), so there is no cost trade-off that would ever justify
trusting a possibly-stale index instead.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.errors import NotFoundError

from src.category.classifier import mean_pairwise_similarity

log = logging.getLogger("category.chroma_store")

# EphemeralClient only (persist_path=None): its in-process state is shared
# across instances, so a second build_chroma_index() call in the same
# process (two tests, two experiment runs) would collide on a fixed name --
# a per-call uuid suffix keeps every ephemeral build's namespace independent.
_EPHEMERAL_COLLECTION_PREFIX = "categories-"
# PersistentClient only: a real directory on disk has no in-process
# collision risk to guard against, so a fixed name is correct here, not a
# simplification -- see get_collection/delete_collection below, which
# depend on the name being stable across calls to find what was already
# persisted.
_PERSISTED_COLLECTION_NAME = "categories"


@dataclass
class ChromaIndex:
    """An in-memory Chroma collection, already populated (or, in
    persist_path mode, a handle onto an on-disk one that may have just been
    reused rather than rebuilt -- callers cannot tell the difference and do
    not need to)."""

    collection: chromadb.Collection
    size: int  # document count, for n_results in chroma_scores


def _corpus_hash(documents: dict[str, str], model_id: str, dimensionality: int) -> str:
    """Fingerprint of exactly what a Chroma index was built FROM -- sorted
    (code, document text) pairs, the embedding model id, and the
    dimensionality -- so a change to any of them (corpus_mode edited,
    embedding model swapped) is detected and forces a rebuild, rather than
    silently searching stale vectors under a corpus that has since moved
    on. Sorted so the hash does not depend on dict iteration order."""
    digest = hashlib.sha256()
    for code in sorted(documents):
        digest.update(code.encode("utf-8"))
        digest.update(documents[code].encode("utf-8"))
    digest.update(model_id.encode("utf-8"))
    digest.update(str(dimensionality).encode("utf-8"))
    return digest.hexdigest()


def build_chroma_index(
    documents: dict[str, str],
    embeddings: dict[str, list[float]],
    *,
    persist_path: Path | None = None,
    model_id: str | None = None,
    dimensionality: int | None = None,
) -> ChromaIndex:
    """Build (or, in persist_path mode, reuse) a Chroma collection from
    ALREADY-CACHED embeddings (GeminiEmbedder) -- Chroma is never asked to
    compute its own embeddings here. Its default embedding model would
    change two variables at once (embedding model AND vector store), making
    the store-vs-store comparison this module exists for meaningless;
    `documents` supplies only the code set (its text is not re-embedded,
    except as input to the staleness hash below), `embeddings` supplies the
    actual vectors.

    persist_path=None (default): EphemeralClient, in-memory, never touches
    disk -- the ablation harness's own path (scripts/classify_category.py),
    unchanged since F-11. Rebuilds from cached embeddings in under a
    second, so a persisted store was never needed there.

    persist_path=<a directory> (production, src/graph/pipeline.py):
    PersistentClient at that path, survives across process runs. Rebuilt
    only when the corpus_hash stored in the collection's own metadata no
    longer matches what `documents`/`embeddings`/`model_id`/`dimensionality`
    hash to now -- see this module's docstring, STALENESS. model_id and
    dimensionality are REQUIRED in this mode (to compute that hash); their
    absence is a caller bug, not a runtime condition to degrade from.
    """
    if persist_path is None:
        client = chromadb.EphemeralClient()
        collection_name = _EPHEMERAL_COLLECTION_PREFIX + uuid.uuid4().hex
        collection = client.create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})
        codes = list(documents.keys())
        collection.add(ids=codes, embeddings=[embeddings[code] for code in codes])
        return ChromaIndex(collection=collection, size=len(codes))

    if model_id is None or dimensionality is None:
        raise ValueError("build_chroma_index requires model_id and dimensionality when persist_path is given")

    corpus_hash = _corpus_hash(documents, model_id, dimensionality)
    client = chromadb.PersistentClient(path=str(persist_path))

    try:
        collection = client.get_collection(name=_PERSISTED_COLLECTION_NAME)
    except NotFoundError:
        log.info("Building Chroma index at %s: no persisted collection found.", persist_path)
        collection = None

    if collection is not None:
        if collection.metadata.get("corpus_hash") == corpus_hash:
            return ChromaIndex(collection=collection, size=collection.count())
        log.info(
            "Rebuilding Chroma index at %s: corpus_hash changed (was %s, now %s).",
            persist_path,
            collection.metadata.get("corpus_hash"),
            corpus_hash,
        )
        client.delete_collection(_PERSISTED_COLLECTION_NAME)

    collection = client.create_collection(
        name=_PERSISTED_COLLECTION_NAME, metadata={"hnsw:space": "cosine", "corpus_hash": corpus_hash}
    )
    codes = list(documents.keys())
    collection.add(ids=codes, embeddings=[embeddings[code] for code in codes])
    return ChromaIndex(collection=collection, size=len(codes))


def chroma_scores(index: ChromaIndex, query_embedding: list[float]) -> dict[str, float]:
    """code -> cosine similarity to query_embedding. Queries with
    n_results=len(documents) so every code gets a score, matching
    tfidf_scores' contract -- never a partial top-k.

    Chroma returns DISTANCES, not similarities. In cosine space Chroma
    defines distance = 1 - similarity, so similarity = 1 - distance here.
    Getting this backwards silently INVERTS the ranking -- the least
    relevant document would score highest -- so this conversion is the
    first thing to check if chroma_scores and embedding_scores ever
    disagree by more than HNSW's approximation error.
    """
    result = index.collection.query(query_embeddings=[query_embedding], n_results=index.size)
    codes = result["ids"][0]
    distances = result["distances"][0]
    return {code: 1.0 - distance for code, distance in zip(codes, distances, strict=True)}


def chroma_mean_pairwise_similarity(embeddings: dict[str, list[float]]) -> float:
    """Same vector space as the "embedding" retrieval path -- delegates to
    classifier.py's mean_pairwise_similarity rather than duplicating the
    pairwise computation through Chroma itself."""
    return mean_pairwise_similarity(embeddings)
