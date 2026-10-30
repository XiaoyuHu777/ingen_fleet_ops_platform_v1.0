FROM ghcr.io/astral-sh/uv:0.9.6 AS uv
FROM python:3.11.14-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services ./services
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
RUN useradd --system --uid 10001 --no-create-home registry
USER registry
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "services.registry.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
