# syntax=docker/dockerfile:1.7

ARG UV_VERSION=0.11.28
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM node:22-bookworm-slim

ARG OPENCODE_VERSION=1.17.11

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

RUN npm install --global "opencode-ai@${OPENCODE_VERSION}" \
    && npm cache clean --force \
    && opencode --version

RUN groupadd --gid 10001 algoforge \
    && useradd --uid 10001 --gid algoforge --create-home --shell /usr/sbin/nologin algoforge

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY --chown=algoforge:algoforge . .
RUN mkdir -p /app/outputs \
    && chown algoforge:algoforge /app/outputs

USER algoforge

EXPOSE 7860

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import json, urllib.request; payload=json.load(urllib.request.urlopen('http://127.0.0.1:7860/healthz', timeout=2)); raise SystemExit(payload.get('status') != 'ok')"]

CMD ["python", "-m", "harness_agent.cli", "serve-web", "--host", "0.0.0.0", "--port", "7860", "--output-root", "/app/outputs/web_runs"]
