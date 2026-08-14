# Multi-stage build for zephyrex server.
# Stage 1: build wheels. Stage 2: slim runtime.

ARG PYTHON_IMAGE=python:3.11-slim-bookworm

# ---- Build stage ----
FROM ${PYTHON_IMAGE} AS builder

RUN set -eux && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libffi-dev \
        libssl-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install ".[prod]"


# ---- Runtime stage ----
FROM ${PYTHON_IMAGE}

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8

RUN set -eux && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 \
        libgomp1 \
        curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /server

RUN groupadd --system --gid 10001 app && \
    useradd --system --uid 10001 --gid app --shell /usr/sbin/nologin --no-create-home app

COPY --chown=app:app . /server

USER app

EXPOSE 1996

ENTRYPOINT ["python3", "-m", "zephyrex", "run"]
