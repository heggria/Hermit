# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source
COPY hermit/ hermit/
COPY setup.py ./

# Install the package itself
RUN uv pip install --no-deps -e .

ENV PATH="/app/.venv/bin:$PATH"

# ~/.hermit is mounted as a volume so config/memory/sessions persist
VOLUME ["/root/.hermit"]

ENTRYPOINT ["hermit"]
CMD ["chat"]
