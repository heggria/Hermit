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

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(pad_event_to=30),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=cast(TextIO, log_stream)),
        wrapper_class=structlog.make_filtering_bound_logger(resolved),
    )
