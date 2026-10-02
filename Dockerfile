# syntax=docker/dockerfile:1
FROM python:3.14-slim-trixie AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        build-essential \
        cmake \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock .python-version ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra cpu --no-install-project

COPY README.md ./
COPY pratevenn/ ./pratevenn/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra cpu

FROM python:3.14-slim-trixie AS cpu

RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        ca-certificates \
        curl \
        libgomp1 \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home pratevenn \
    && mkdir -p /models /data \
    && chown -R pratevenn:pratevenn /models /data \
    && chmod 700 /data

WORKDIR /app

COPY --from=builder --chown=pratevenn:pratevenn /app/.venv /app/.venv
COPY --from=builder --chown=pratevenn:pratevenn /app/pratevenn /app/pratevenn
COPY --from=builder --chown=pratevenn:pratevenn /app/pyproject.toml /app/pyproject.toml

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PRATEVENN_MODEL_DIR=/models \
    PRATEVENN_DATA_DIR=/data \
    PRATEVENN_HOST=0.0.0.0

EXPOSE 8000
VOLUME ["/models", "/data"]

USER pratevenn
ENTRYPOINT ["pratevenn"]
CMD ["start", "--host", "0.0.0.0", "--port", "8000", "--model-dir", "/models"]
