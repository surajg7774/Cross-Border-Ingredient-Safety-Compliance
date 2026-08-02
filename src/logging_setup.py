# DESIGN RULE: this module owns one piece of I/O -- opening the log file --
# the same role src/category/embedder.py's disk cache and src/horizon/
# load.py play for their stages. It is deliberately NOT a logging.Logger
# setup: no levels, no rotation, no structured records -- just a tee of
# whatever a script already prints, verbatim, to a plain text file.
"""Tee console output to a plain-text log file.

Every scripts/*.py entry point builds a `rich.console.Console()` at import
time with no explicit `file=` argument, which means Console reads
`sys.stdout` fresh on every print rather than caching it -- so replacing
`sys.stdout` with a tee AFTER that Console already exists still works,
without touching a single console.print() call site.

The terminal side of the tee is untouched (same bytes, same ANSI codes, so
colour on a real terminal looks exactly as it did before). The file side
strips rich's ANSI escape codes via Text.from_ansi() -- rich's own parser,
not a hand-rolled regex -- since those codes are the likely reason pasted
terminal output fails to transfer cleanly.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from rich.text import Text

from config import settings


class _TeeStream:
    """A minimal file-like object: every write goes to the original stream
    unchanged, and (ANSI-stripped) to the log file. Delegates every other
    attribute (isatty, encoding, ...) to the original stream, so rich's own
    terminal detection on the wrapped stream is unaffected."""

    def __init__(self, original: IO[str], log_file: IO[str]) -> None:
        self._original = original
        self._log_file = log_file

    def write(self, text: str) -> int:
        written = self._original.write(text)
        if text:
            self._log_file.write(Text.from_ansi(text).plain)
            self._log_file.flush()
        return written

    def flush(self) -> None:
        self._original.flush()

    def isatty(self) -> bool:
        return self._original.isatty()

    def __getattr__(self, name):
        return getattr(self._original, name)


def setup_run_log(name: str) -> Path:
    """Start teeing sys.stdout to data/outputs/logs/<name>_<timestamp>.log
    and return its path. Call once, near the top of a script's main() --
    every console.print() after this point (via the script's own module-
    level `console = Console()`) is captured, ANSI-free, in the file.
    """
    settings.LOG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = settings.LOG_OUTPUT_DIR / f"{name}_{timestamp}.log"

    log_file = log_path.open("w", encoding="utf-8")
    sys.stdout = _TeeStream(sys.stdout, log_file)
    return log_path
