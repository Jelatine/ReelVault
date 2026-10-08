FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY reelvault_vision ./reelvault_vision
RUN uv sync --frozen --no-dev
RUN useradd --uid 10001 --create-home vision && mkdir /models && chown vision:vision /models
USER vision
ENV REELVAULT_VISION_TOKEN=""
EXPOSE 8091
ENTRYPOINT ["/app/.venv/bin/reelvault-vision", "--cache", "/models"]
CMD ["--host", "0.0.0.0"]
