# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src src

RUN uv build --wheel --out-dir /dist

FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/hermit

RUN groupadd --system hermit \
    && useradd --system --create-home --gid hermit hermit

WORKDIR /app

COPY --from=builder /dist/*.whl /tmp/dist/
RUN python -m pip install /tmp/dist/*.whl \
    && rm -rf /tmp/dist

USER hermit

VOLUME ["/home/hermit/.hermit"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-c", "import hermit; from hermit.surfaces.cli.main import app; print(app.info.name)"]

ENTRYPOINT ["hermit"]
CMD ["chat"]
