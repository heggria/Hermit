# Observation

Logging setup and runtime observability.

`configure_logging()` initializes both stdlib `logging` and `structlog` for the
Hermit process. It configures a structlog pipeline with timestamping, log-level
annotation, and console rendering. Noisy HTTP-layer loggers (`httpx`,
`httpcore`) are suppressed to WARNING level. When no custom stream is provided,
structlog uses its built-in lazy stderr factory so that pytest-xdist workers do
not trigger "I/O operation on closed file" errors from early stderr closure.

::: hermit.runtime.observation.logging.setup
