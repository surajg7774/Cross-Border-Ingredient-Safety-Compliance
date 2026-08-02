"""CLI diagnostic: why do category similarity scores all land in the same
narrow band? Builds the corpus for a given config (--config, default
"baseline", i.e. corpus_mode="full") and embeds it -- GeminiEmbedder, with
its own disk cache (data/reference/category_embeddings.json), so a repeat
run of the SAME config costs nothing; the first run of a NEW config costs
~155 embedding calls, once.

    uv run python scripts/diagnose_embeddings.py
    uv run python scripts/diagnose_embeddings.py --config name-only
    uv run python scripts/diagnose_embeddings.py --config enriched

If unrelated categories average similar-to-each-other, the corpus documents
are too alike for cosine similarity to discriminate between them, and that
explains why every retrieval-side change has moved the numbers so little --
this is what the corpus-construction experiments (src/category/experiment.py's
corpus_mode) test: whether reshaping the DOCUMENT text (not the query, not
the retrieval method) narrows that gap. Reports this run's numbers against
the measured "full" baseline (mean pairwise similarity 0.7847, rank-1-to-
rank-10 gap 0.0335), and appends one row to data/outputs/experiments.csv
(stage="corpus_diagnostic") per run.
"""

import csv
import json
import statistics
import sys
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table

from config import settings
from scripts.score_category import EXPERIMENTS_CSV_HEADER
from src.category.classifier import cosine_similarity
from src.category.corpus import build_corpus
from src.category.embedder import DIMENSIONALITY, GeminiEmbedder, _cache_key
from src.category.experiment import CONFIGS
from src.logging_setup import setup_run_log

console = Console()

CACHE_PATH = settings.REFERENCE_DIR / "category_embeddings.json"
# MEASURED baseline (config="baseline", corpus_mode="full") -- every new
# config's numbers are reported as a delta against these.
BASELINE_MEAN_SIMILARITY = 0.7847
BASELINE_RANK_GAP = 0.0335


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_hit_miss_counts(documents: dict[str, str], cache: dict, model_id: str) -> tuple[int, int]:
    """(hits, misses) against the ON-DISK cache, checked BEFORE embedding --
    a stale hit here would mean two different document texts collapsed onto
    the same cache key (they cannot: the key is sha256(text + model_id +
    dimensionality), so any text change changes the key) or that this
    config's text is byte-for-byte identical to one already embedded (only
    "full" vs itself, never a genuinely different corpus_mode). A first run
    of a NEW config must show misses close to len(documents); a repeat run
    of the SAME config must show hits close to len(documents) -- print both
    so that is directly checkable, not assumed.
    """
    hits = sum(1 for text in documents.values() if _cache_key(text, model_id, DIMENSIONALITY) in cache)
    return hits, len(documents) - hits


def _report_pairwise_similarity(vectors: dict[str, list[float]], categories: dict) -> tuple[float, float, float]:
    codes = sorted(vectors)
    pairs = [(a, b, cosine_similarity(vectors[a], vectors[b])) for a, b in combinations(codes, 2)]
    scores = [s for _, _, s in pairs]
    mean_score, min_score, max_score = sum(scores) / len(scores), min(scores), max(scores)

    console.print(f"\n[bold]Pairwise similarity across {len(codes)} category documents[/bold] ({len(pairs)} pairs)")
    console.print(f"  min: {min_score:.4f}  mean: {mean_score:.4f}  max: {max_score:.4f}")
    console.print(
        f"  delta vs baseline mean ({BASELINE_MEAN_SIMILARITY:.4f}): {mean_score - BASELINE_MEAN_SIMILARITY:+.4f}"
    )

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

    return mean_score, min_score, max_score


def _report_document_lengths(documents: dict[str, str], categories: dict) -> tuple[int, float, int]:
    lengths = {code: len(text) for code, text in documents.items()}
    all_lengths = list(lengths.values())
    min_len, median_len, max_len = min(all_lengths), statistics.median(all_lengths), max(all_lengths)

    console.print(f"\n[bold]Document text length[/bold] (characters, {len(all_lengths)} documents)")
    console.print(f"  min: {min_len}  median: {median_len:.0f}  max: {max_len}")

    table = Table(title="5 shortest category documents")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("Length")
    table.add_column("Text")
    for code, length in sorted(lengths.items(), key=lambda kv: kv[1])[:5]:
        table.add_row(code, categories[code].name, str(length), documents[code])
    console.print(table)

    return min_len, median_len, max_len


def _report_rank_gap(vectors: dict[str, list[float]], categories: dict, embedder: GeminiEmbedder) -> float | None:
    """The fixed sample query: Chipsmain's product-scope query text, read
    from its already-classified output. The query TEXT does not depend on
    corpus_mode (only the corpus documents do), so the same query is
    comparable across every config's run -- embedded fresh here (cached
    after the first call) rather than looked up in a static dict, so this
    works for a brand-new config too, not just whichever config happened to
    embed it first.
    """
    chipsmain = _load_json(settings.CATEGORY_OUTPUT_DIR / "Chipsmain.json")
    if chipsmain is None:
        console.print(
            "\n[yellow]No data/outputs/category/Chipsmain.json -- run "
            "scripts/classify_category.py first to see the rank1-vs-rank10 gap.[/yellow]"
        )
        return None

    product_result = next((r for r in chipsmain["results"] if r["query"]["scope"] == "product"), None)
    if product_result is None:
        console.print("\n[yellow]Chipsmain.json has no product-scope result to diagnose.[/yellow]")
        return None

    query_text = product_result["query"]["text"]
    query_vector = embedder.embed_query(query_text)

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

    if len(top10) != 10:
        return None
    gap = top10[0][1] - top10[9][1]
    console.print(
        f"\nRank-1 vs rank-10 score gap: {gap:.4f}  (rank1={top10[0][1]:.4f}, rank10={top10[9][1]:.4f})"
    )
    console.print(f"  delta vs baseline gap ({BASELINE_RANK_GAP:.4f}): {gap - BASELINE_RANK_GAP:+.4f}")
    return gap


def _append_experiment_row(config_name: str, mean_similarity: float, n: int) -> None:
    is_new = not settings.EXPERIMENTS_CSV.exists()
    settings.EXPERIMENTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with settings.EXPERIMENTS_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(EXPERIMENTS_CSV_HEADER)
        # Same 11-column shape score_category.py's "category"-stage rows
        # use, so both stages accumulate into one comparable table --
        # recall/mrr/index_build_seconds/truth_version are not applicable
        # to a corpus diagnostic and are left blank, not zero (zero would
        # misread as a measured score of 0).
        writer.writerow(
            [
                datetime.now(UTC).isoformat(),
                "corpus_diagnostic",
                config_name,
                "embedding",
                "",
                "",
                "",
                n,
                f"{mean_similarity:.4f}",
                "",
                "",
            ]
        )
    console.print(f"Appended results to [cyan]{settings.EXPERIMENTS_CSV}[/cyan]")


def main(
    config_name: Annotated[
        str, typer.Option("--config", help=f"Corpus/query config to diagnose: one of {sorted(CONFIGS)}.")
    ] = "baseline",
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    log_path = setup_run_log("diagnose_embeddings") if log else None
    if config_name not in CONFIGS:
        console.print(f"[red]Unknown --config {config_name!r}. Choose one of: {sorted(CONFIGS)}[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)
    config = CONFIGS[config_name]

    raw_categories = _load_json(settings.REFERENCE_DIR / "food_categories.json", [])
    eu_fip = _load_json(settings.REFERENCE_DIR / "eu_fip.json", [])
    codex_ins = _load_json(settings.REFERENCE_DIR / "codex_ins.json", [])
    categories, documents, _flags = build_corpus(raw_categories, config, eu_fip, codex_ins)
    model_id = settings.EMBEDDING_MODEL

    cache_before = _load_json(CACHE_PATH, {})
    hits, misses = _cache_hit_miss_counts(documents, cache_before, model_id)
    console.print(f"[bold]Cache[/bold] (config={config_name}): {hits} hit, {misses} miss (of {len(documents)})")

    embedder = GeminiEmbedder()
    vectors = embedder.embed_documents(documents)

    mean_score, _min_score, _max_score = _report_pairwise_similarity(vectors, categories)
    _report_document_lengths(documents, categories)
    _report_rank_gap(vectors, categories, embedder)

    _append_experiment_row(config_name, mean_score, len(documents))

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
