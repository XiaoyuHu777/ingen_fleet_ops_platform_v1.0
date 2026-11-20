FROM ghcr.io/astral-sh/uv:0.9.6 AS uv
FROM python:3.11.14-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group robot --no-install-project
COPY agent ./agent
COPY services/__init__.py ./services/__init__.py
COPY services/registry/__init__.py services/registry/schemas.py ./services/registry/
RUN useradd --system --uid 10002 --no-create-home agent \
    && install -d -o agent -g agent /var/lib/ingen-agent
USER agent
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 9101
CMD ["python", "-m", "agent"]
