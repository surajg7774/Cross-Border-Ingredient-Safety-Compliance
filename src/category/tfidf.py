# DESIGN RULE: pure functions over documents passed in as an argument -- no
# file reads, no printing, same rules as the rest of src/category/.
"""TF-IDF retrieval over the category corpus -- a non-neural control against
embeddings. MEASURED: mean pairwise cosine similarity between 155 UNRELATED
category documents is 0.7847 in embedding space, and "Seasoning" ->
"Seasonings and condiments" is a literal token match that keyword search
handles by construction. If TF-IDF outperforms embeddings on this corpus,
the weak link is the retrieval method, not the query text.
"""

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity as _sklearn_cosine_similarity


@dataclass
class TfidfIndex:
    """A fitted TF-IDF vectoriser plus the corpus matrix, code-aligned."""

    codes: list[str]
    vectorizer: TfidfVectorizer
    matrix: object  # scipy.sparse matrix, one row per code, aligned with `codes`


def build_tfidf_index(documents: dict[str, str]) -> TfidfIndex:
    """Fit a TF-IDF vectoriser over the corpus documents."""
    codes = list(documents.keys())
    vectorizer = TfidfVectorizer()
    matrix = vectorizer.fit_transform(documents[code] for code in codes)
    return TfidfIndex(codes=codes, vectorizer=vectorizer, matrix=matrix)


def tfidf_scores(index: TfidfIndex, query_text: str) -> dict[str, float]:
    """code -> cosine similarity between query_text and that document, under
    the corpus's fitted vocabulary. Always returns one score per code in the
    index, even 0.0 for a query sharing no vocabulary with a document."""
    query_vector = index.vectorizer.transform([query_text])
    similarities = _sklearn_cosine_similarity(query_vector, index.matrix)[0]
    return dict(zip(index.codes, similarities.tolist(), strict=True))


def tfidf_mean_pairwise_similarity(index: TfidfIndex) -> float:
    """Mean cosine similarity across every distinct pair of corpus
    documents under TF-IDF -- the same diagnostic measure computed for
    embeddings (see src/category/classifier.py's mean_pairwise_similarity
    and scripts/diagnose_embeddings.py), so the two retrieval methods are
    comparable on how well each discriminates this corpus.
    """
    n = len(index.codes)
    if n < 2:
        return 0.0
    similarity_matrix = _sklearn_cosine_similarity(index.matrix)
    upper_triangle = similarity_matrix[np.triu_indices(n, k=1)]
    return float(upper_triangle.mean())
