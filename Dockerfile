# Ingestion pipeline worker.
#
# Runs in a Linux container rather than on the Windows host for two
# reasons. The obvious one is that it matches every other service:
# restart: unless-stopped, so it comes back after a reboot instead of
# dying at logout. The less obvious one is that Celery's default prefork
# pool does not work on Windows — a host worker is stuck with
# `--pool=solo` and no concurrency at all. On Linux that problem simply
# does not exist.
#
# Nothing here reads a path or a hostname from code: DOCUMENT_ROOT,
# POSTGRES_SPM_HOST, REDIS_HOST and QDRANT_URL all arrive as environment
# variables (see infra/docker-compose.yml). Moving the pipeline into a
# container is a configuration change, which is the property
# pipeline/config.py exists to guarantee.

FROM python:3.13-slim

# tini reaps zombies and forwards signals, so `docker stop` actually stops
# the worker rather than waiting out the timeout.
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

COPY pipeline/ ./pipeline/

# Never run as root: the corpus is mounted read-only anyway, but a worker
# that only ever reads files and writes to databases has no need for it.
RUN useradd --create-home --uid 10001 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["celery", "-A", "pipeline.tasks", "worker", "--loglevel=info", "--concurrency=2"]
