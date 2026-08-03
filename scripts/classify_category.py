"""CLI entry point: classify which EU food categories a label (or each of
its composite components) plausibly belongs to.

    uv run python scripts/classify_category.py data/outputs/resolution/Chipsmain.json
    uv run python scripts/classify_category.py --all
    uv run python scripts/classify_category.py --all --config tfidf
    uv run python scripts/classify_category.py --all --config chromadb

Loads the reference datasets, then scores queries by embedding (GeminiEmbedder,
cached, exhaustive numpy cosine similarity), by TF-IDF, or by ChromaDB
(config.retrieval_method) -- tfidf never instantiates the embedder and makes
no API calls at all; chromadb calls the embedder for the SAME vectors as
"embedding" but indexes/searches them through Chroma instead of numpy, a
vector-STORE ablation (see src/category/chroma_store.py). Calls
src.category.classifier, writes data/outputs/category/<name>.json, and prints
a summary table. All I/O and network calls live here (or inside embedder.py's
own cache layer) -- src/category/corpus.py, filter.py, classifier.py,
tfidf.py, and chroma_store.py never read a file, call the network, or print
anything.
"""

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer
from rich.console import Console
from rich.table import Table

from config import settings
from src.category.chroma_store import (
    build_chroma_index,
    chroma_mean_pairwise_similarity,
    chroma_scores,
)
from src.category.classifier import (
    TOP_K_RETRIEVED,
    build_queries,
    classify,
    embedding_scores,
    mean_pairwise_similarity,
    mmr_scores,
    reciprocal_rank_fusion,
    top_k,
)
from src.category.corpus import build_corpus
from src.category.embedder import GeminiEmbedder
from src.category.experiment import CONFIGS, ExperimentConfig
from src.category.multiquery import generate_paraphrases
from src.category.tfidf import build_tfidf_index, tfidf_mean_pairwise_similarity, tfidf_scores
from src.logging_setup import setup_run_log
from src.reference_data import load_eu_fip
from src.resolve.schemas import ResolvedItem

console = Console()

# EVALUATION FIXTURE, not part of the system: this file stands in for the
# description a real user would type via --description below, so the
# ablation (src/category/experiment.py's description_scope) could be
# measured against a fixed, repeatable set of labels before --description
# existed. When scoring the 9-label eval set, this is where the text comes
# from; for an actual user's product, --description is the real input and
# always takes precedence (see _resolve_description).
DESCRIPTIONS_PATH = Path("data/labels/descriptions.json")

# (scores, query_variants) -- query_variants is the extra paraphrase texts
# actually searched (multi_query only, [] for every other strategy), so
# _classify_file can pass them through to classify() for the output JSON.
ScoreQuery = Callable[[str], tuple[dict[str, float], list[str]]]


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_corpus(config: ExperimentConfig, eu_fip: list[dict], codex_ins: list[dict]) -> tuple[dict, dict]:
    raw_categories = _load_json(settings.REFERENCE_DIR / "food_categories.json", [])
    categories, documents, flags = build_corpus(raw_categories, config, eu_fip, codex_ins)
    for flag in flags:
        console.print(
            f"[yellow]Note:[/yellow] category {flag.code} description excluded -- "
            f"references {flag.referenced_codes} from a different top-level tree"
        )
    return categories, documents


def _build_scorer(
    config: ExperimentConfig, documents: dict[str, str]
) -> tuple[ScoreQuery, float, float | None]:
    """Returns (score_query, corpus_mean_similarity, index_build_seconds).

    "tfidf": fits a TF-IDF index over the corpus documents and scores query
    text against it -- the embedder is never instantiated, no API calls at
    all. "chromadb": embeds the corpus via GeminiEmbedder (cached) -- the
    SAME vectors "embedding" would use -- then indexes them in an in-memory
    Chroma collection and searches THAT instead of exhaustive numpy cosine
    similarity; a vector-STORE ablation, not an embedding-model one (see
    src/category/chroma_store.py). "embedding": embeds the corpus once via
    GeminiEmbedder (cached), then embeds each query text on demand, scored
    by exhaustive numpy cosine similarity -- exact and instant at 155
    documents, so it builds no separate index at all.

    corpus_mean_similarity is the same diagnostic measure
    (scripts/diagnose_embeddings.py) computed once per run, over whichever
    vector space this config actually used -- so the retrieval methods are
    comparable on how well each discriminates the corpus.

    index_build_seconds is the cost side of the tfidf/chromadb comparison
    against plain embedding -- None (blank in experiments.csv) for
    "embedding", which builds no index to time.

    Within "embedding", config.mmr_lambda / multi_query / hybrid are
    composable REFINEMENTS of the same vector space (see ExperimentConfig's
    docstring) -- checked in this order, mutually exclusive in every
    CONFIGS entry so far (their combined effect is untested; if more than
    one is set, hybrid wins, then multi_query, then mmr_lambda).
    """
    if config.retrieval_method == "tfidf":
        start = time.perf_counter()
        index = build_tfidf_index(documents)
        index_build_seconds = time.perf_counter() - start
        corpus_mean_similarity = tfidf_mean_pairwise_similarity(index)

        def score_query(text: str) -> tuple[dict[str, float], list[str]]:
            return tfidf_scores(index, text), []

        return score_query, corpus_mean_similarity, index_build_seconds

    if config.retrieval_method == "chromadb":
        embedder = GeminiEmbedder()
        embeddings = embedder.embed_documents(documents)
        corpus_mean_similarity = chroma_mean_pairwise_similarity(embeddings)

        start = time.perf_counter()
        index = build_chroma_index(documents, embeddings)
        index_build_seconds = time.perf_counter() - start

        def score_query(text: str) -> tuple[dict[str, float], list[str]]:
            return chroma_scores(index, embedder.embed_query(text)), []

        return score_query, corpus_mean_similarity, index_build_seconds

    embedder = GeminiEmbedder()
    embeddings = embedder.embed_documents(documents)
    corpus_mean_similarity = mean_pairwise_similarity(embeddings)

    if config.hybrid:
        tfidf_index = build_tfidf_index(documents)

        def score_query(text: str) -> tuple[dict[str, float], list[str]]:
            embedding_ranking = top_k(embedding_scores(embedder.embed_query(text), embeddings), TOP_K_RETRIEVED)
            tfidf_ranking = top_k(tfidf_scores(tfidf_index, text), TOP_K_RETRIEVED)
            return reciprocal_rank_fusion([embedding_ranking, tfidf_ranking]), []

        return score_query, corpus_mean_similarity, None

    if config.multi_query > 0:

        def score_query(text: str) -> tuple[dict[str, float], list[str]]:
            paraphrases = generate_paraphrases(text, config.multi_query, settings.PRIMARY_MODEL)
            rankings = [
                top_k(embedding_scores(embedder.embed_query(variant), embeddings), TOP_K_RETRIEVED)
                for variant in (text, *paraphrases)
            ]
            return reciprocal_rank_fusion(rankings), paraphrases

        return score_query, corpus_mean_similarity, None

    if config.mmr_lambda is not None:

        def score_query(text: str) -> tuple[dict[str, float], list[str]]:
            return mmr_scores(embedder.embed_query(text), embeddings, config.mmr_lambda), []

        return score_query, corpus_mean_similarity, None

    def score_query(text: str) -> tuple[dict[str, float], list[str]]:
        return embedding_scores(embedder.embed_query(text), embeddings), []

    return score_query, corpus_mean_similarity, None


def _resolve_description(label: str, cli_description: str | None, descriptions: dict) -> tuple[str | None, str]:
    """Precedence: an explicit --description wins over the
    descriptions.json fixture entry for that label. Returns (description,
    source) -- source is recorded in the output JSON so the eval can tell
    a hand-typed description from a fixture one.
    """
    if cli_description is not None:
        return cli_description, "cli"
    fixture_description = descriptions.get(label)
    if fixture_description is not None:
        return fixture_description, "descriptions_file"
    return None, "none"


def _strategy_label(config: ExperimentConfig) -> str:
    """A human-readable name for which retrieval strategy is active, for
    the output JSON -- so a reader can tell how a ranking was produced
    without cross-referencing config.name against CONFIGS' field values.
    "none" covers plain embedding/tfidf/chromadb retrieval, no strategy
    layered on top."""
    if config.hybrid:
        return "hybrid-rrf"
    if config.multi_query > 0:
        return f"mqr-{config.multi_query}"
    if config.mmr_lambda is not None:
        return f"mmr-{config.mmr_lambda}"
    return "none"


def _classify_file(
    extraction_path: Path,
    resolution_path: Path,
    categories: dict,
    eu_fip: list,
    score_query: ScoreQuery,
    corpus_mean_similarity: float,
    index_build_seconds: float | None,
    config: ExperimentConfig,
    descriptions: dict,
    cli_description: str | None,
) -> None:
    if not resolution_path.exists():
        console.print(f"[yellow]{resolution_path.name}: no resolution file, skipping[/yellow]")
        return

    extraction = _load_json(extraction_path, {})
    if not extraction or extraction.get("extraction") is None:
        console.print(f"[yellow]{extraction_path.name}: no extraction to classify (gate stopped)[/yellow]")
        return

    resolution = _load_json(resolution_path, {})
    items = extraction["extraction"]["items"]
    resolved_items = [ResolvedItem.model_validate(item) for item in resolution["items"]]
    product_name = extraction["gate"].get("product_name")
    product_descriptor = extraction["gate"].get("product_descriptor")
    user_description, description_source = _resolve_description(extraction_path.stem, cli_description, descriptions)

    queries = build_queries(items, resolved_items, product_name, product_descriptor, config, user_description)
    results = []
    for query in queries:
        if not query.text.strip():
            # A component with nothing food_ingredient/compound among its
            # children and no product_name/descriptor to fall back on --
            # e.g. Pepsimain's "FLAVOUR" component, whose only child is a
            # flavouring substance (out of scope, excluded from the query).
            # Nothing to score; there is no category to retrieve.
            console.print(
                f"[yellow]{extraction_path.name}: component "
                f"{query.component_label!r} has no query text, skipping[/yellow]"
            )
            continue
        scores, query_variants = score_query(query.text)
        results.append(classify(query, scores, categories, eu_fip, config, query_variants))

    table = Table(title=extraction_path.name)
    table.add_column("Scope")
    table.add_column("Component")
    table.add_column("Query")
    table.add_column("Top-3 (code: name, score, permitted)")
    table.add_column("Filter")

    for result in results:
        top3_text = "\n".join(
            f"{c.code}: {c.name} ({c.similarity:.2f}, {'permitted' if c.permitted else 'not permitted'})"
            for c in result.top3
        )
        filter_text = f"empty_intersection={result.empty_intersection}"
        if result.excluded_additives:
            filter_text += f"\nexcluded (no permitted rows): {', '.join(result.excluded_additives)}"
        table.add_row(
            result.query.scope,
            result.component_label or "(whole label)",
            result.query.text or "—",
            top3_text or "—",
            filter_text,
        )
    console.print(table)

    settings.CATEGORY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.CATEGORY_OUTPUT_DIR / extraction_path.name
    payload = {
        "config": config.name,
        "retrieval_method": config.retrieval_method,
        "strategy": _strategy_label(config),
        "corpus_mean_similarity": corpus_mean_similarity,
        "index_build_seconds": index_build_seconds,
        "description_source": description_source,
        "results": [r.model_dump() for r in results],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    console.print(f"Saved to [cyan]{out_path}[/cyan]\n")


def main(
    resolution_path: Annotated[
        Path | None, typer.Argument(help="Path to one data/outputs/resolution/*.json file.")
    ] = None,
    all_files: Annotated[
        bool, typer.Option("--all", help="Classify every file in data/outputs/resolution/.")
    ] = False,
    config_name: Annotated[
        str,
        typer.Option(
            "--config",
            help=f"Experiment config: one of {sorted(CONFIGS)}.",
        ),
    ] = "baseline",
    description: Annotated[
        str | None,
        typer.Option(
            "--description",
            help=(
                "Product description, e.g. 'Glucose biscuits'. Overrides "
                "data/labels/descriptions.json for this run."
            ),
        ),
    ] = None,
    log: Annotated[
        bool, typer.Option("--log/--no-log", help="Tee console output to a log file (default on).")
    ] = True,
) -> None:
    """Classify one resolution file, or every resolution file with --all."""
    log_path = setup_run_log("classify_category") if log else None
    if not resolution_path and not all_files:
        console.print("[red]Pass a file path, or use --all.[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)
    if config_name not in CONFIGS:
        console.print(f"[red]Unknown --config {config_name!r}. Choose one of: {sorted(CONFIGS)}[/red]")
        if log_path:
            console.print(f"Log: {log_path}")
        raise typer.Exit(1)
    if all_files and description is not None:
        console.print(
            "[yellow]--description with --all applies the SAME description to EVERY label -- "
            "almost never what you want. Pass a single resolution file path instead if you "
            "meant one product.[/yellow]"
        )
    config = CONFIGS[config_name]

    eu_fip = load_eu_fip(settings.REFERENCE_DIR / "eu_fip.json")
    codex_ins = _load_json(settings.REFERENCE_DIR / "codex_ins.json", [])
    descriptions = _load_json(DESCRIPTIONS_PATH, {})
    categories, documents = _load_corpus(config, eu_fip, codex_ins)
    score_query, corpus_mean_similarity, index_build_seconds = _build_scorer(config, documents)
    build_text = f", index build: {index_build_seconds:.4f}s" if index_build_seconds is not None else ""
    console.print(
        f"Corpus mean pairwise similarity ({config.retrieval_method}): "
        f"{corpus_mean_similarity:.4f}{build_text}\n"
    )

    if all_files:
        paths = sorted(settings.RESOLUTION_OUTPUT_DIR.glob("*.json"))
        if not paths:
            console.print(f"[yellow]No files found in {settings.RESOLUTION_OUTPUT_DIR}[/yellow]")
            if log_path:
                console.print(f"Log: {log_path}")
            raise typer.Exit(1)
        for path in paths:
            extraction_path = settings.EXTRACTION_OUTPUT_DIR / path.name
            _classify_file(
                extraction_path,
                path,
                categories,
                eu_fip,
                score_query,
                corpus_mean_similarity,
                index_build_seconds,
                config,
                descriptions,
                description,
            )
    else:
        extraction_path = settings.EXTRACTION_OUTPUT_DIR / resolution_path.name
        _classify_file(
            extraction_path,
            resolution_path,
            categories,
            eu_fip,
            score_query,
            corpus_mean_similarity,
            index_build_seconds,
            config,
            descriptions,
            description,
        )

    if log_path:
        console.print(f"Log: {log_path}")


if __name__ == "__main__":
    typer.run(main)
