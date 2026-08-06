"""Postgres access for the ingestion pipeline.

**The pipeline connects as `spm_admin`, never `spm_app`.** These are not
interchangeable and the distinction is a security boundary, not a
convenience:

  * `spm_admin` owns the tables and therefore **bypasses row-level
    security**. The pipeline legitimately needs cross-project reach — it
    processes every document in every project.
  * `spm_app` is the *chat backend's* role: `SELECT`-only and RLS-scoped
    to a single project per transaction. It could not run this pipeline
    even if asked to, which is the point.

See INFRASTRUCTURE.md 7c. This module deliberately exposes only the admin
connection; a pipeline module that wants `spm_app` is a pipeline module
doing something wrong.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from pipeline.config import get_settings


@contextmanager
def admin_connection(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Open a connection as the pipeline's role, from settings only.

    Defaults to a transaction that commits on clean exit and rolls back on
    exception, which is what every stage wants: a run that dies halfway
    must not leave the database half-updated.
    """
    settings = get_settings()
    with psycopg.connect(settings.postgres.dsn, autocommit=autocommit) as conn:
        yield conn
