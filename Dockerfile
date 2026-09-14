# Two images from one file: the ingestion pipeline and the chat application.
#
# Both run in Linux containers rather than on the Windows host, for the same
# reason every other service does: restart: unless-stopped, so they come
# back after a crash or a reboot instead of dying with whatever terminal —
# or session — happened to start them. The chat app ran on the host until
# 2026-09-14, and stopped every time the session that launched it ended.
#
# The pipeline has a second reason: Celery's default prefork pool does not
# work on Windows — a host worker is stuck with `--pool=solo` and no
# concurrency at all. On Linux that problem simply does not exist.
#
# **Two targets, one shared base.** Both install the same requirements, so
# that layer is built once. But each target copies only its own code, so a
# change to the chat app rebuilds the app image alone and the pipeline
# containers are not recreated — and the pipeline image carries no web
# server code it never runs.
#
#   docker compose build pipeline-worker   # target: pipeline
#   docker compose build chat-api          # target: app
#
# Nothing here reads a path or a hostname from code: DOCUMENT_ROOT,
# POSTGRES_SPM_HOST, REDIS_HOST, QDRANT_URL and the tracing endpoints all
# arrive as environment variables (see infra/docker-compose.yml). Moving a
# component into a container is a configuration change, which is the
# property pipeline/config.py exists to guarantee.

# ---------------------------------------------------------------------------
# base — the interpreter, the dependencies, and an unprivileged user
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS base

# tini reaps zombies and forwards signals, so `docker stop` actually stops
# the process rather than waiting out the timeout.
# procps supplies pgrep, which beat's healthcheck uses — python:slim does
# not ship it, and a healthcheck whose command is missing reports the
# container unhealthy forever rather than reporting nothing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Bake the tokenizer's BPE data into the image. tiktoken otherwise fetches
# it from the internet on first use, which would turn a network hiccup into
# a chunking failure at run time — for a file that never changes.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# Never run as root. Neither process needs it: the pipeline only reads the
# corpus (mounted read-only) and writes to databases; the app only serves.
RUN useradd --create-home --uid 10001 enjaz

ENTRYPOINT ["/usr/bin/tini", "--"]

# ---------------------------------------------------------------------------
# pipeline — the ingestion worker and its scheduler
# ---------------------------------------------------------------------------
FROM base AS pipeline

COPY pipeline/ ./pipeline/
RUN chown -R enjaz:enjaz /app
USER enjaz

CMD ["celery", "-A", "pipeline.tasks", "worker", "--loglevel=info", "--concurrency=2"]

# ---------------------------------------------------------------------------
# app — the chat API and the single-file frontend it serves
# ---------------------------------------------------------------------------
FROM base AS app

# The app imports settings from pipeline/config.py, and tracing from
# observability/. It does not import the pipeline's stages or evaluation/.
COPY pipeline/ ./pipeline/
COPY observability/ ./observability/
COPY Application/ ./Application/

# ⚠️ Application/frontend/assets/fonts/ holds a licensed font that is
# gitignored but NOT dockerignored: it is copied from the local checkout
# into this local image, which is never pushed anywhere. Built from a fresh
# clone without the files, the image still works and the page falls back
# to a system font.
RUN chown -R enjaz:enjaz /app
USER enjaz

EXPOSE 8000
CMD ["python", "-m", "Application.backend.api"]
