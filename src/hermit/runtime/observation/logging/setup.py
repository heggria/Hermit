from __future__ import annotations

import logging
import sys
from io import TextIOBase
from typing import IO, TextIO, cast

import structlog


def configure_logging(level: str = "INFO", *, stream: IO[str] | TextIOBase | None = None) -> None:
    resolved = getattr(logging, level.upper(), logging.INFO)
    log_stream = stream or sys.stderr
    logging.basicConfig(level=resolved, format="%(message)s", stream=log_stream, force=True)

    for noisy_logger in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    kwargs: dict[str, object] = {
        "processors": [
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(pad_event_to=30),
        ],
        "wrapper_class": structlog.make_filtering_bound_logger(resolved),
    }
    # Only set an explicit logger factory when a custom stream was provided.
    # The default (sys.stderr) should use structlog's built-in factory which
    # resolves stderr lazily, avoiding "I/O operation on closed file" under
    # pytest-xdist where worker stderr can be closed early.
    if stream is not None:
        kwargs["logger_factory"] = structlog.PrintLoggerFactory(file=cast(TextIO, stream))
    structlog.configure(**kwargs)  # type: ignore[arg-type]
