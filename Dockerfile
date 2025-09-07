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

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# Copy venv and app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

# Non-root user
RUN useradd -m app && chown -R app:app /app /opt/venv
USER app

# Default CMD can be overridden by docker-compose per service
CMD ["python", "-c", "print('magent2 image ready')"]
