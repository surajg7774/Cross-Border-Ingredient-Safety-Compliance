# DESIGN RULE: this is a SEPARATE module from src/model_call.py, not an
# addition to it, so that importing it (and, transitively,
# langchain-google-genai) stays scoped to whatever opts into it --
# currently only src/report/narrator.py, behind the MODEL_BACKEND setting.
# Every other direct-call site (src/extractors/gemini.py, src/agent/
# resolver_agent.py, src/category/multiquery.py) keeps importing ONLY
# src/model_call.py's plumbing (strip_markdown_fences, with_retry) and
# never sees langchain-google-genai at all, even at import time -- this
# migration phase is narrator-only, per instruction.
"""A text-generation Protocol -- "send one prompt, get raw text back" --
with two implementations, following the same shape src/agent/
resolver_agent.py already uses for its own LLM Protocol
(GeminiLLM/MockLLM):

    class TextGenerator(Protocol):
        def complete(self, prompt: str, model_id: str) -> str: ...

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
  ChatResult.llm_output, which .invoke() discards. LangChainTextGenerator
  below calls .generate() instead of .invoke() specifically to recover it
  via result.llm_output["prompt_feedback"].

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
  would never catch. LangChainTextGenerator unwraps this back to the
  original APIError via `exc.__cause__` (Python's `raise ... from e` sets
  it) before with_retry ever sees it, so retryability is judged
  identically to the native path. A ServerError (5xx, e.g. 500/503) is
  NOT wrapped this way -- google.genai only catches ClientError there, so
  those already propagate as the same APIError subclass either path takes.
"""

from typing import Protocol

from google.genai import errors
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

from config import settings
from src.model_call import with_retry

MAX_RATE_LIMIT_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
# Same retryable set as src/extractors/gemini.py and src/report/narrator.py:
# 429 = rate limited, 500/503 = transient server-side overload. Both
# implementations here use the SAME tuning -- they must behave identically,
# not just similarly.
RETRYABLE_STATUS_CODES = {429, 500, 503}


class EmptyResponseError(RuntimeError):
    """Raised when the model returns no text -- blocked or truncated, not a
    crash. Same meaning, one per provider-facing module, as
    src/extractors/gemini.py's and src/report/narrator.py's own exception
    of the same name."""


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

        def _call() -> str:
            try:
                result = chat.generate([[HumanMessage(content=prompt)]])
            except ChatGoogleGenerativeAIError as exc:
                # Unwrap back to the raw APIError with_retry expects -- see
                # this module's docstring on why ChatGoogleGenerativeAI
                # re-raises a different exception type for a 429.
                if isinstance(exc.__cause__, errors.APIError):
                    raise exc.__cause__ from exc
                raise

            message = result.generations[0][0].message
            text = _extract_message_text(message.content)
            if not text:
                finish_reason = message.response_metadata.get("finish_reason")
                prompt_feedback = (result.llm_output or {}).get("prompt_feedback")
                raise EmptyResponseError(
                    f"model returned no text (finish_reason={finish_reason}, "
                    f"prompt_feedback={prompt_feedback})"
                )
            return text

        return with_retry(
            _call,
            max_retries=MAX_RATE_LIMIT_RETRIES,
            initial_backoff_seconds=INITIAL_BACKOFF_SECONDS,
            retryable_status_codes=RETRYABLE_STATUS_CODES,
        )


def get_text_generator() -> TextGenerator:
    """The TextGenerator settings.MODEL_BACKEND selects -- "native"
    (default, current behaviour) or "langchain" (opt-in)."""
    if settings.MODEL_BACKEND == "langchain":
        return LangChainTextGenerator()
    return GeminiTextGenerator()
