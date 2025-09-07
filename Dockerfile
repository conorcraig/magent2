# syntax=docker/dockerfile:1.7

########## Builder (uv) ##########
FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS builder
ENV UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app

# Cache dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Install project into venv
COPY . .
RUN uv sync --frozen

########## Runtime ##########
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user before copying so ownership can be set at copy time
RUN useradd -m app

# Copy venv and app with correct ownership without a slow recursive chown layer
COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --from=builder --chown=app:app /app /app

USER app

# Default CMD can be overridden by docker-compose per service
CMD ["python", "-c", "print('magent2 image ready')"]
