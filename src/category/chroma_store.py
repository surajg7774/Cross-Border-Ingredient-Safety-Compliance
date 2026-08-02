# DESIGN RULE: pure functions over documents/embeddings passed in as
# arguments -- no file reads, no printing, same rules as the rest of
# src/category/. build_chroma_index does construct a chromadb client, but
# that client is IN-MEMORY (EphemeralClient) and never touches disk or the
# network, so this stays consistent with the "no I/O" rule the same way
# TfidfVectorizer's in-memory fit does in tfidf.py.
"""ChromaDB retrieval over the category corpus -- a vector-STORE ablation,
not an embedding-model one. The original project design specified ChromaDB
as the vector store; scoping to EU-only reduced the corpus to 155 category
documents, at which exhaustive cosine similarity in numpy (classifier.py's
cosine_similarity/embedding_scores) is exact and instant. This module adds
Chroma back as a MEASURED ALTERNATIVE rather than a silent swap, so
experiments.csv records whether it changes anything and what it costs.

EXPECTED: identical ranking to the "embedding" path, since both use the
same vectors and the same cosine metric over the same 155 documents. Any
difference is either HNSW approximation (Chroma builds an approximate
index; exhaustive numpy search does not) or a scoring-convention bug. A
large divergence means the latter -- check the distance-to-similarity
conversion first.
"""

import uuid
from dataclasses import dataclass

import chromadb

from src.category.classifier import mean_pairwise_similarity

# Chroma collection names must be 3-512 chars from [a-zA-Z0-9._-]. A fixed
# name is NOT safe here: EphemeralClient's in-process state is shared across
# instances, so a second build_chroma_index() call in the same process (e.g.
# two tests, or two experiment runs in one script) would collide with
# "Collection [categories] already exists" -- discovered via pytest running
# more than one test that builds an index. A per-call uuid suffix keeps every
# build's namespace independent.
_COLLECTION_PREFIX = "categories-"


@dataclass
class ChromaIndex:
    """An in-memory Chroma collection, already populated."""

    collection: chromadb.Collection
    size: int  # document count, for n_results in chroma_scores


def build_chroma_index(documents: dict[str, str], embeddings: dict[str, list[float]]) -> ChromaIndex:
    """Build an in-memory Chroma collection from ALREADY-CACHED embeddings
    (GeminiEmbedder) -- Chroma is never asked to compute its own embeddings
    here. Its default embedding model would change two variables at once
    (embedding model AND vector store), making the store-vs-store
    comparison this module exists for meaningless; `documents` supplies
    only the code set (its text is not re-embedded), `embeddings` supplies
    the actual vectors.

    EphemeralClient: in-memory, no persistence directory. The corpus
    rebuilds from cached embeddings in under a second, so a persisted store
    would only add a stale-index failure mode for no gain.
    """
    client = chromadb.EphemeralClient()
    collection_name = _COLLECTION_PREFIX + uuid.uuid4().hex
    collection = client.create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})
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
