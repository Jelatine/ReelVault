# syntax=docker/dockerfile:1

# ---- frontend ----
FROM node:26-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Optional JavaScript solver runtime; only copied into the opt-in image.
FROM denoland/deno:bin-2.9.7 AS deno

# ---- runtime ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ARG REELVAULT_LINK_IMPORT_EXTRA=0
RUN --mount=from=deno,source=/deno,target=/tmp/deno \
    if [ "$REELVAULT_LINK_IMPORT_EXTRA" = "1" ]; then install -m 755 /tmp/deno /usr/local/bin/deno; fi
COPY backend/pyproject.toml backend/uv.lock ./
RUN if [ "$REELVAULT_LINK_IMPORT_EXTRA" = "1" ]; then \
        uv sync --frozen --no-dev --no-install-project --extra link-import; \
    else uv sync --frozen --no-dev --no-install-project; fi
COPY backend/reelvault ./reelvault
RUN if [ "$REELVAULT_LINK_IMPORT_EXTRA" = "1" ]; then \
        uv sync --frozen --no-dev --extra link-import; \
    else uv sync --frozen --no-dev; fi
COPY --from=web /web/dist ./reelvault/static

RUN useradd --system --uid 1000 --home /data reelvault \
    && mkdir -p /data && chown reelvault:reelvault /data
USER reelvault

ENV PATH=/app/.venv/bin:$PATH \
    REELVAULT_IN_DOCKER=1 \
    REELVAULT_DATA_DIR=/data \
    REELVAULT_PORT=8080
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["reelvault"]
