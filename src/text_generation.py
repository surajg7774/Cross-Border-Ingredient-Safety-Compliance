# DESIGN RULE: this is a SEPARATE module from src/model_call.py, not an
# addition to it, so that importing it (and, transitively,
# langchain-google-genai) stays scoped to whatever opts into it --
# src/report/narrator.py, src/category/multiquery.py, and (as of this
# phase) src/extractors/gemini.py, all behind the MODEL_BACKEND setting.
# src/agent/resolver_agent.py keeps importing ONLY src/model_call.py's
# plumbing (strip_markdown_fences, with_retry) and never sees
# langchain-google-genai at all, even at import time -- the agent and
# embeddings (src/category/embedder.py) are explicitly out of scope for
# this migration, per instruction.
"""Two Protocols for the two request shapes every direct google-genai call
site in this project needs, each with two implementations:

    class TextGenerator(Protocol):
        def complete(self, prompt: str, model_id: str) -> str: ...

    class VisionGenerator(Protocol):
        def complete(self, prompt: str, image_bytes: bytes, mime_type: str, model_id: str) -> str: ...

TextGenerator follows the same shape src/agent/resolver_agent.py already
uses for its own LLM Protocol (GeminiLLM/MockLLM). VisionGenerator is a
SEPARATE Protocol, not TextGenerator with an optional image parameter --
src/extractors/gemini.py (the only caller) uses different retry tuning
(retry_delay_seconds, honouring a 429's server-supplied RetryInfo delay --
see RETRY_DELAY_MARGIN_SECONDS below) than src/report/narrator.py and
src/category/multiquery.py's plain-exponential policy, and forcing that
tuning decision onto TextGenerator's two existing, already-tested,
text-only callers was rejected as unnecessary risk to code that already
works.

    GeminiTextGenerator     -- the existing google-genai call path
                               (client.models.generate_content), through
                               src.model_call.with_retry. NO BEHAVIOUR
                               CHANGE from narrator.py's previous inline
                               _call_model -- this is that same code,
                               moved.
    LangChainTextGenerator  -- langchain_google_genai's
                               ChatGoogleGenerativeAI, ALSO through
                               src.model_call.with_retry (its own internal
                               retry loop is disabled -- max_retries=1 --
                               so with_retry is the only retry policy in
                               effect, same as the native path).
    GeminiVisionGenerator   -- client.models.generate_content with an
                               inline image Part, through with_retry using
                               gemini.py's OWN tuning (retry_delay_seconds +
                               RETRY_DELAY_MARGIN_SECONDS). NO BEHAVIOUR
                               CHANGE from gemini.py's previous inline
                               _call_model -- this is that same code, moved.
    LangChainVisionGenerator -- ChatGoogleGenerativeAI given a HumanMessage
                               with a "media" content block alongside the
                               prompt, through the SAME response-handling
                               logic LangChainTextGenerator uses (factored
                               into the private _generate_via_langchain
                               below, so this logic is written once, not
                               copy-pasted per Protocol).

WHAT LANGCHAIN CANNOT FULLY REPRODUCE -- MEASURED against the installed
langchain-google-genai==4.3.2 / langchain-core==1.5.3, not guessed:

- finish_reason SURVIVES. ChatGoogleGenerativeAI puts it on
  AIMessage.response_metadata["finish_reason"] whenever at least one
  candidate exists (langchain_google_genai.chat_models._response_to_result
  sets it in `generation_info`; langchain_core's own
  `_gen_info_and_msg_metadata` merges that into the final message's
  response_metadata for every .generate()/.invoke() call).

- prompt_feedback (the top-level, PROMPT-was-blocked signal, distinct from
  a per-candidate finish_reason) is NOT exposed by a plain .invoke() --
  ChatGoogleGenerativeAI puts it only on the batch-level
  ChatResult.llm_output, which .invoke() discards. Both LangChain
  implementations below call .generate() instead of .invoke() specifically
  to recover it via result.llm_output["prompt_feedback"].

- Even via .generate(), there is one gap LEFT UNFIXED because the library
  itself has it: when Gemini returns ZERO candidates (the prompt itself
  was blocked before generation started), langchain_google_genai's
  non-streaming branch builds `AIMessage("")` with response_metadata={} --
  no finish_reason, nothing about WHY the candidate list is empty attached
  to the message. It is the streaming branch, not the one .generate() ever
  takes, that attaches prompt_feedback to the message itself
  (chat_models.py's _response_to_result, the `if not response.candidates`
  branch). llm_output["prompt_feedback"] (see above) is what actually
  fills this specific gap for a non-streaming call; without reaching for
  it, the zero-candidates case would carry no diagnostic detail at all
  through the LangChain path, where the native path always has
  response.prompt_feedback directly on hand.

- A retryable error is not always raised as google.genai.errors.APIError.
  ChatGoogleGenerativeAI's own request path (still, underneath,
  self.client.models.generate_content) catches google.genai.errors.
  ClientError (4xx, e.g. a 429) and re-raises langchain_google_genai's own
  ChatGoogleGenerativeAIError instead (chat_models.py's
  _handle_client_error) -- a DIFFERENT exception hierarchy, with no `.code`
  of its own, that src.model_call.with_retry's `except errors.APIError`
  would never catch. _generate_via_langchain (used by both LangChain
  implementations below) unwraps this back to the original APIError via
  `exc.__cause__` (Python's `raise ... from e` sets it) before with_retry
  ever sees it, so retryability is judged identically to the native path.
  A ServerError (5xx, e.g. 500/503) is NOT wrapped this way -- google.genai
  only catches ClientError there, so those already propagate as the same
  APIError subclass either path takes.

WHAT WAS CHECKED FOR MULTIMODAL INPUT SPECIFICALLY, and what was not:

- The REQUEST is structurally equivalent, verified by reading the
  installed library's source, not assumed. langchain_google_genai's
  `"media"` content-block type (chat_models.py's _convert_to_parts)
  builds `Blob(data=..., mime_type=...)` wrapped in `Part(inline_data=...)`
  -- both classes imported directly from google.genai.types, the SAME
  classes gemini.py uses. google.genai.types.Part.from_bytes itself is
  exactly `Part(inline_data=Blob(data=data, mime_type=mime_type))` when no
  media_resolution is given (gemini.py never gives one) -- so the two
  paths construct the identical typed object before either reaches the
  same underlying self.client.models.generate_content call. Raw `bytes`
  pass straight into Blob.data without a forced base64 round-trip either
  way.

- NOT verified, and this is an open question, not a settled one: whether
  ChatGoogleGenerativeAI applies any different default generation_config
  or safety_settings for multimodal content versus text-only content.
  Nothing in chat_models.py's source suggests a modality-conditional
  branch for this, but that is a static-reading conclusion, not a live
  one -- no real (non-mocked) call comparing native vs langchain output
  on an actual image has been made. Do not treat "structurally equivalent
  request" as "measured to produce equivalent output" -- see
  src/extractors/gemini.py's _cache_key for what this means for caching,
  and get a live comparison before fully trusting MODEL_BACKEND=langchain
  for real extraction runs.
"""

from typing import Protocol

from google.genai import errors, types
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

from config import settings
from src.model_call import retry_delay_seconds, with_retry

MAX_RATE_LIMIT_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
# Same retryable set as src/extractors/gemini.py and src/report/narrator.py:
# 429 = rate limited, 500/503 = transient server-side overload. All four
# implementations in this module use the SAME set -- they must behave
# identically, not just similarly.
RETRYABLE_STATUS_CODES = {429, 500, 503}
# GeminiVisionGenerator/LangChainVisionGenerator ONLY -- src/extractors/
# gemini.py's own tuning (added to the server's own RetryInfo.retryDelay
# before sleeping; see src/model_call.py's retry_delay_seconds), not shared
# with the plain-exponential TextGenerator implementations above.
RETRY_DELAY_MARGIN_SECONDS = 1.0


class EmptyResponseError(RuntimeError):
    """Raised when the model returns no text -- blocked or truncated, not a
    crash. The ONE definition every module that delegates to
    get_text_generator()/get_vision_generator() raises and lets propagate --
    src/report/narrator.py, src/category/multiquery.py, and
    src/extractors/gemini.py neither define nor need their own same-named
    class any more (narrator.py catches broadly regardless of exception
    type; multiquery.py's generate_paraphrases() and gemini.py's _run()
    both let whatever this raises propagate/get caught by name unchanged --
    gemini.py's _run() catches this SPECIFICALLY, by name, as one of its
    three retry-triggering exception types, so this class identity is
    load-bearing there, not cosmetic). src/agent/resolver_agent.py uses a
    different LLM Protocol entirely (GeminiLLM/MockLLM) and does not define
    or raise this exception at all."""


def _extract_message_text(content: object) -> str:
    """AIMessage.content is a plain string for most models -- but MEASURED,
    with PRIMARY_MODEL=gemini-3.5-flash, a successful ("finish_reason=STOP")
    reply comes back as a LIST of content blocks instead:
    [{"type": "text", "text": "...", "extras": {"signature": "..."}}],
    sometimes interleaved with non-text blocks (e.g. "thinking"). The
    previous `... if isinstance(content, str) else ""` treated that whole
    shape as empty and raised EmptyResponseError on a perfectly good
    response -- this is the fix.

    Never degrades an unrecognised shape to "" -- that silent degradation
    is exactly how the bug above survived. A shape this function doesn't
    understand raises TypeError naming the actual type and repr, so a
    future format change fails loudly instead of looking like an empty
    response again.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif isinstance(block, dict):
                continue  # a non-text block (e.g. "thinking") -- ignored, not an error
            else:
                raise TypeError(
                    "unrecognised content block in AIMessage.content list: "
                    f"{type(block).__name__}: {block!r}"
                )
        return "".join(parts)
    raise TypeError(f"unrecognised AIMessage.content shape: {type(content).__name__}: {content!r}")


def _generate_via_langchain(chat: ChatGoogleGenerativeAI, content: str | list) -> str:
    """One ChatGoogleGenerativeAI.generate() call, wrapped in the response
    handling BOTH LangChain implementations below need identically: unwrap
    ChatGoogleGenerativeAIError back to the raw APIError with_retry expects,
    extract text via _extract_message_text (handles the Gemini 3.x
    content-block-list shape), and raise EmptyResponseError -- with
    finish_reason AND prompt_feedback, never just one -- on an empty result.

    `content` is a single HumanMessage's content: a bare prompt string for
    LangChainTextGenerator, or a content-block list (prompt string + a
    "media" block) for LangChainVisionGenerator. This function does not
    care which -- everything here is RESPONSE-shape handling, which does
    not depend on what the request contained. Written once, here, after the
    previous duplicate-fix drift this session already found once (see
    src/rules/row_selection.py's module docstring for that story) --
    not copy-pasted a second time.

    Retry policy (max_retries, backoff, retry_delay_fn) is NOT this
    function's concern -- each caller wraps its own call to this in its own
    with_retry(...) with its own tuning, exactly as gemini.py's direct call
    used to and narrator.py/multiquery.py still do.
    """
    try:
        result = chat.generate([[HumanMessage(content=content)]])
    except ChatGoogleGenerativeAIError as exc:
        # Unwrap back to the raw APIError with_retry expects -- see this
        # module's docstring on why ChatGoogleGenerativeAI re-raises a
        # different exception type for a 429.
        if isinstance(exc.__cause__, errors.APIError):
            raise exc.__cause__ from exc
        raise

    message = result.generations[0][0].message
    text = _extract_message_text(message.content)
    if not text:
        finish_reason = message.response_metadata.get("finish_reason")
        prompt_feedback = (result.llm_output or {}).get("prompt_feedback")
        raise EmptyResponseError(
            f"model returned no text (finish_reason={finish_reason}, prompt_feedback={prompt_feedback})"
        )
    return text


class TextGenerator(Protocol):
    def complete(self, prompt: str, model_id: str) -> str:
        """Send `prompt` to `model_id`, return the raw text response
        (fences, if any, are NOT stripped here -- callers, e.g.
        src/report/narrator.py's narrate(), already do that with
        src.model_call.strip_markdown_fences on whatever this returns).
        Raises on failure (network, empty response, exhausted retries);
        callers catch broadly and fall back to a deterministic result."""
        ...


class GeminiTextGenerator:
    """The existing google-genai call path -- client.models.generate_content
    -- through src.model_call.with_retry. This is narrator.py's previous
    inline _call_model, moved here unchanged."""

    def __init__(self) -> None:
        from google import genai

        self._client = genai.Client(api_key=settings.GOOGLE_API_KEY)

    def complete(self, prompt: str, model_id: str) -> str:
        def _call() -> str:
            response = self._client.models.generate_content(model=model_id, contents=[prompt])
            if response.text is None:
                finish_reason = None
                if response.candidates:
                    finish_reason = response.candidates[0].finish_reason
                raise EmptyResponseError(
                    f"model returned no text (finish_reason={finish_reason}, "
                    f"prompt_feedback={response.prompt_feedback})"
                )
            return response.text

        return with_retry(
            _call,
            max_retries=MAX_RATE_LIMIT_RETRIES,
            initial_backoff_seconds=INITIAL_BACKOFF_SECONDS,
            retryable_status_codes=RETRYABLE_STATUS_CODES,
        )


class LangChainTextGenerator:
    """langchain_google_genai's ChatGoogleGenerativeAI, through the SAME
    src.model_call.with_retry policy. See this module's docstring for
    exactly what does and does not survive the translation from the raw
    google-genai response shape."""

    def complete(self, prompt: str, model_id: str) -> str:
        chat = ChatGoogleGenerativeAI(
            model=model_id,
            google_api_key=settings.GOOGLE_API_KEY,
            # Disable the SDK's OWN retry loop (default 6 attempts) so
            # with_retry below is the only retry policy in effect -- per
            # langchain_google_genai's own docstring, max_retries=1 (not 0)
            # means "just the initial request, no retries"; 0 means "use
            # the Google SDK's default" instead.
            max_retries=1,
        )

        return with_retry(
            lambda: _generate_via_langchain(chat, prompt),
            max_retries=MAX_RATE_LIMIT_RETRIES,
            initial_backoff_seconds=INITIAL_BACKOFF_SECONDS,
            retryable_status_codes=RETRYABLE_STATUS_CODES,
        )


class VisionGenerator(Protocol):
    def complete(self, prompt: str, image_bytes: bytes, mime_type: str, model_id: str) -> str:
        """Send `prompt` and one image to `model_id`, return the raw text
        response (fences, if any, are NOT stripped here -- callers, e.g.
        src/extractors/gemini.py's GeminiExtractor._run(), already do that
        with src.model_call.strip_markdown_fences on whatever this
        returns). `mime_type` is computed by the caller (gemini.py's own
        _mime_type, via PIL) -- this Protocol does not sniff image formats
        itself. Raises on failure (network, empty response, exhausted
        retries); gemini.py's _run() catches EmptyResponseError SPECIFICALLY
        by name, as one of its retry-triggering failure types -- see that
        class's own docstring."""
        ...


class GeminiVisionGenerator:
    """The existing google-genai call path for image+text extraction --
    client.models.generate_content with an inline image Part -- through
    src.model_call.with_retry, using src/extractors/gemini.py's OWN tuning
    (retry_delay_seconds + RETRY_DELAY_MARGIN_SECONDS, honouring a 429's
    server-supplied RetryInfo delay -- see model_call.py's DESIGN RULE on
    keeping each call site's tuning where it is). This is gemini.py's
    previous inline _call_model, moved here unchanged."""

    def __init__(self) -> None:
        from google import genai

        self._client = genai.Client(api_key=settings.GOOGLE_API_KEY)

    def complete(self, prompt: str, image_bytes: bytes, mime_type: str, model_id: str) -> str:
        part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        def _call() -> str:
            response = self._client.models.generate_content(model=model_id, contents=[prompt, part])
            if response.text is None:
                # The SDK returns None text when a response is blocked
                # (safety) or truncated (e.g. hit max output tokens) --
                # treat that as a failed attempt, not a crash.
                finish_reason = None
                if response.candidates:
                    finish_reason = response.candidates[0].finish_reason
                raise EmptyResponseError(
                    f"model returned no text (finish_reason={finish_reason}, "
                    f"prompt_feedback={response.prompt_feedback})"
                )
            return response.text

        return with_retry(
            _call,
            max_retries=MAX_RATE_LIMIT_RETRIES,
            initial_backoff_seconds=INITIAL_BACKOFF_SECONDS,
            retryable_status_codes=RETRYABLE_STATUS_CODES,
            retry_delay_fn=retry_delay_seconds,
            retry_delay_margin_seconds=RETRY_DELAY_MARGIN_SECONDS,
        )


class LangChainVisionGenerator:
    """langchain_google_genai's ChatGoogleGenerativeAI, given a HumanMessage
    whose content is [prompt, {"type": "media", "mime_type": ..., "data":
    image_bytes}] -- through the SAME _generate_via_langchain response
    handling LangChainTextGenerator uses, but with_retry'd using
    src/extractors/gemini.py's OWN tuning (retry_delay_seconds +
    RETRY_DELAY_MARGIN_SECONDS), not the plain-exponential policy the text
    generators use. See this module's docstring, "WHAT WAS CHECKED FOR
    MULTIMODAL INPUT SPECIFICALLY", for what is and is not verified about
    this request shape before trusting it for a real extraction run."""

    def complete(self, prompt: str, image_bytes: bytes, mime_type: str, model_id: str) -> str:
        chat = ChatGoogleGenerativeAI(
            model=model_id,
            google_api_key=settings.GOOGLE_API_KEY,
            max_retries=1,
        )
        content = [prompt, {"type": "media", "mime_type": mime_type, "data": image_bytes}]

        return with_retry(
            lambda: _generate_via_langchain(chat, content),
            max_retries=MAX_RATE_LIMIT_RETRIES,
            initial_backoff_seconds=INITIAL_BACKOFF_SECONDS,
            retryable_status_codes=RETRYABLE_STATUS_CODES,
            retry_delay_fn=retry_delay_seconds,
            retry_delay_margin_seconds=RETRY_DELAY_MARGIN_SECONDS,
        )


def get_text_generator() -> TextGenerator:
    """The TextGenerator settings.MODEL_BACKEND selects -- "native"
    (default, current behaviour) or "langchain" (opt-in)."""
    if settings.MODEL_BACKEND == "langchain":
        return LangChainTextGenerator()
    return GeminiTextGenerator()


def get_vision_generator() -> VisionGenerator:
    """The VisionGenerator settings.MODEL_BACKEND selects -- "native"
    (default, current behaviour) or "langchain" (opt-in). See this module's
    docstring, "WHAT WAS CHECKED FOR MULTIMODAL INPUT SPECIFICALLY", before
    trusting the langchain path here for a real extraction run."""
    if settings.MODEL_BACKEND == "langchain":
        return LangChainVisionGenerator()
    return GeminiVisionGenerator()
