"""Application settings, loaded from environment variables / .env."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the compliance checker.

    Model IDs live in .env rather than being hardcoded here because
    free-tier model availability on Google AI Studio changes often —
    verify current model IDs before setting these.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    GOOGLE_API_KEY: str
    PRIMARY_MODEL: str
    SECONDARY_MODEL: str
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    # Which src/text_generation.py TextGenerator/VisionGenerator
    # implementation src/report/narrator.py, src/category/multiquery.py,
    # and src/extractors/gemini.py call -- "native" (google-genai directly,
    # unchanged default behaviour) or "langchain" (ChatGoogleGenerativeAI,
    # same retry policy/model IDs, opt-in). See src/text_generation.py's
    # module docstring for what "langchain" cannot reproduce -- including,
    # for gemini.py specifically, what has and has not been verified for
    # multimodal (image) input.
    MODEL_BACKEND: Literal["native", "langchain"] = "native"
    # Which store src/graph/pipeline.py's classify node searches the
    # category corpus through -- "numpy" (default, exhaustive cosine
    # similarity, src/category/classifier.py's embedding_scores, unchanged
    # behaviour) or "chroma" (src/category/chroma_store.py, a persisted
    # index built from the SAME cached embeddings, adopted for store
    # properties -- persistence, metadata filtering -- not accuracy;
    # docs/findings.md F-11 measured identical ranking to numpy, at extra
    # cost, and F-11 is still true). Rankings must be identical between the
    # two; see tests/test_category.py's cross-store test.
    RETRIEVAL_STORE: Literal["numpy", "chroma"] = "numpy"
    CHROMA_PERSIST_DIR: Path = Path(".cache/chroma")
    CACHE_DIR: Path = Path(".cache")
    OUTPUT_DIR: Path = Path("data/outputs")
    EXTRACTION_OUTPUT_DIR: Path = OUTPUT_DIR / "extraction"
    EXTRACTION_GOLDEN_DIR: Path = Path("data/golden/extraction")
    RESOLUTION_OUTPUT_DIR: Path = OUTPUT_DIR / "resolution"
    RESOLUTION_GOLDEN_DIR: Path = Path("data/golden/resolution")
    CATEGORY_OUTPUT_DIR: Path = OUTPUT_DIR / "category"
    CATEGORY_TRUTH_PATH: Path = Path("data/golden/category_truth.json")
    EXPERIMENTS_CSV: Path = OUTPUT_DIR / "experiments.csv"
    VERDICT_OUTPUT_DIR: Path = OUTPUT_DIR / "verdict"
    VERDICT_GOLDEN_DIR: Path = Path("data/golden/verdict")
    SUBSTITUTES_OUTPUT_DIR: Path = OUTPUT_DIR / "substitutes"
    HORIZON_OUTPUT_DIR: Path = OUTPUT_DIR / "horizon"
    AGENT_OUTPUT_DIR: Path = OUTPUT_DIR / "agent"
    AGENT_TRUTH_PATH: Path = Path("data/golden/agent_truth.json")
    LOG_OUTPUT_DIR: Path = OUTPUT_DIR / "logs"
    REFERENCE_DIR: Path = Path("data/reference")

    # Optional: the report screen's "email this report" feature (src/report/
    # email.py). All five OPTIONAL and default to None -- absent settings
    # disable the feature (src/report/email.py's smtp_config_from_settings()
    # returns None) rather than failing Settings() at import, since most
    # installs of this app will never configure outbound email at all.
    SMTP_HOST: str | None = None
    SMTP_PORT: int | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str | None = None

    # Optional: the (not-yet-wired) horizon news lane's Tavily API key --
    # see src/horizon/search.py's module docstring for the provider
    # decision and for what "not wired" means. Declared here only so
    # Settings() does not fail to construct when this is present in
    # .env -- BaseSettings' default extra="forbid" would otherwise reject
    # any key it doesn't recognise. Nothing reads settings.TAVILY_API_KEY
    # yet: TavilySearchProvider still takes its key as a plain constructor
    # argument (see src/report/email.py's send_report for why -- testable
    # without an environment), not by reading this directly. A
    # get_search_provider()-style factory that DOES read it is later-stage
    # work, not this one.
    TAVILY_API_KEY: str | None = None


settings = Settings()
