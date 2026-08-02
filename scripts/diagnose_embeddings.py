"""CLI diagnostic: why do category similarity scores all land in the same
narrow band? Reads the cached embeddings (data/reference/category_embeddings.json)
and the corpus text -- NO API calls, nothing is embedded here.

    uv run python scripts/diagnose_embeddings.py

If unrelated categories average similar-to-each-other, the corpus documents
are too alike for cosine similarity to discriminate between them, and that
explains why every retrieval-side change has moved the numbers so little.
"""

import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from config import settings
from src.category.classifier import cosine_similarity
from src.category.corpus import build_corpus
from src.category.embedder import DIMENSIONALITY, _cache_key
from src.category.experiment import CONFIGS

console = Console()

CACHE_PATH = settings.REFERENCE_DIR / "category_embeddings.json"


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _cached_vector(text: str, cache: dict, model_id: str) -> list[float] | None:
    return cache.get(_cache_key(text, model_id, DIMENSIONALITY))


def _report_pairwise_similarity(vectors: dict[str, list[float]], categories: dict) -> None:
    codes = sorted(vectors)
    pairs = [(a, b, cosine_similarity(vectors[a], vectors[b])) for a, b in combinations(codes, 2)]
    scores = [s for _, _, s in pairs]

    console.print(f"\n[bold]Pairwise similarity across {len(codes)} category documents[/bold] ({len(pairs)} pairs)")
    console.print(f"  min: {min(scores):.4f}  mean: {sum(scores) / len(scores):.4f}  max: {max(scores):.4f}")

    top_pairs = sorted(pairs, key=lambda p: p[2], reverse=True)[:10]
    table = Table(title="10 most similar category pairs")
    table.add_column("Code A")
    table.add_column("Name A")
    table.add_column("Code B")
    table.add_column("Name B")
    table.add_column("Similarity")
    for a, b, score in top_pairs:
        table.add_row(a, categories[a].name, b, categories[b].name, f"{score:.4f}")
    console.print(table)


def _report_document_lengths(documents: dict[str, str], categories: dict) -> None:
    lengths = {code: len(text) for code, text in documents.items()}
    all_lengths = list(lengths.values())

    console.print(f"\n[bold]Document text length[/bold] (characters, {len(all_lengths)} documents)")
    console.print(
        f"  min: {min(all_lengths)}  mean: {sum(all_lengths) / len(all_lengths):.0f}  max: {max(all_lengths)}"
    )

    table = Table(title="5 shortest category documents")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("Length")
    table.add_column("Text")
    for code, length in sorted(lengths.items(), key=lambda kv: kv[1])[:5]:
        table.add_row(code, categories[code].name, str(length), documents[code])
    console.print(table)


def _report_rank_gap(vectors: dict[str, list[float]], categories: dict, cache: dict, model_id: str) -> None:
    chipsmain = _load_json(settings.CATEGORY_OUTPUT_DIR / "Chipsmain.json")
    if chipsmain is None:
        console.print(
            "\n[yellow]No data/outputs/category/Chipsmain.json -- run "
            "scripts/classify_category.py first to see the rank1-vs-rank10 gap.[/yellow]"
        )
        return

    product_result = next((r for r in chipsmain["results"] if r["query"]["scope"] == "product"), None)
    if product_result is None:
        console.print("\n[yellow]Chipsmain.json has no product-scope result to diagnose.[/yellow]")
        return

    query_text = product_result["query"]["text"]
    query_vector = _cached_vector(query_text, cache, model_id)
    if query_vector is None:
        console.print(
            f"\n[yellow]No cached embedding for Chipsmain's product query text {query_text!r} -- "
            "was it embedded under a different model or config?[/yellow]"
        )
        return

    scored = sorted(
        ((code, cosine_similarity(query_vector, vec)) for code, vec in vectors.items()),
        key=lambda pair: pair[1],
        reverse=True,
    )
    top10 = scored[:10]

    console.print(f"\n[bold]Chipsmain product query[/bold]: {query_text!r}")
    table = Table(title="Top 10 retrieved categories")
    table.add_column("Rank")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("Similarity")
    for i, (code, score) in enumerate(top10, start=1):
        table.add_row(str(i), code, categories[code].name, f"{score:.4f}")
    console.print(table)

    if len(top10) == 10:
        gap = top10[0][1] - top10[9][1]
        console.print(
            f"\nRank-1 vs rank-10 score gap: {gap:.4f}  "
            f"(rank1={top10[0][1]:.4f}, rank10={top10[9][1]:.4f})"
        )


def main() -> None:
    cache = _load_json(CACHE_PATH, {})
    if not cache:
        console.print(f"[red]No cached embeddings at {CACHE_PATH} -- run classify_category.py at least once first.[/red]")
        raise SystemExit(1)

    raw_categories = _load_json(settings.REFERENCE_DIR / "food_categories.json", [])
    categories, documents, _flags = build_corpus(raw_categories, CONFIGS["baseline"])
    model_id = settings.EMBEDDING_MODEL

    vectors = {}
    missing = []
    for code, text in documents.items():
        vector = _cached_vector(text, cache, model_id)
        if vector is None:
            missing.append(code)
        else:
            vectors[code] = vector
    if missing:
        console.print(
            f"[yellow]{len(missing)} categories have no cached embedding (run "
            f"'classify_category.py --all --config baseline' first): "
            f"{missing[:5]}{'...' if len(missing) > 5 else ''}[/yellow]"
        )
        if not vectors:
            raise SystemExit(1)

    _report_pairwise_similarity(vectors, categories)
    _report_document_lengths(documents, categories)
    _report_rank_gap(vectors, categories, cache, model_id)


if __name__ == "__main__":
    main()
