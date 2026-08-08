"""Tests for pipeline/config.py.

Mostly about `resolve_document`, which is the join between a database row
and a file on disk — and the place a whole class of environment bug was
found. `document_links.file_path` used to hold an absolute path; the
Windows host wrote `D:\\MOF_DataSample\\...` and the Linux worker wrote
`/data/...`, so each rewrote all 58 rows on every run and neither could
open a file the other had recorded.

Storing the path relative to DOCUMENT_ROOT fixes that, and this module is
where the fix is enforced: a stale absolute value must be refused with an
instruction, never silently joined into a nonsense path.
"""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from pipeline.config import Settings, get_settings


def rooted(tmp_path) -> Settings:
    return dataclasses.replace(get_settings(), document_root=tmp_path)


# ---------------------------------------------------------------------------
# resolve_document
# ---------------------------------------------------------------------------

def test_a_relative_path_joins_onto_the_root(tmp_path):
    settings = rooted(tmp_path)
    assert settings.resolve_document("PRJ-01/x.docx") == tmp_path / "PRJ-01" / "x.docx"


def test_nested_relative_paths_survive(tmp_path):
    """A real SharePoint export has folders; the scanner stores the whole
    project-relative path, not just the leaf name."""
    settings = rooted(tmp_path)
    resolved = settings.resolve_document("PRJ-01/archive/2025/x.pdf")
    assert resolved == tmp_path / "PRJ-01" / "archive" / "2025" / "x.pdf"


def test_arabic_filenames_resolve(tmp_path):
    """Most of this corpus is Arabic; a path helper that mangles it would
    break the majority of the documents."""
    settings = rooted(tmp_path)
    name = "TEST-PROJECT/_المنهجية التقنية للمشروع.pdf"
    assert settings.resolve_document(name).name == "_المنهجية التقنية للمشروع.pdf"


def test_a_posix_absolute_path_is_refused(tmp_path):
    """What the worker container used to write."""
    with pytest.raises(ValueError, match="must be relative"):
        rooted(tmp_path).resolve_document("/data/PRJ-01/x.docx")


def test_a_windows_absolute_path_is_refused(tmp_path):
    """What the host used to write."""
    with pytest.raises(ValueError, match="must be relative"):
        rooted(tmp_path).resolve_document(r"D:\MOF_DataSample\PRJ-01\x.docx")


def test_the_refusal_says_how_to_fix_it(tmp_path):
    """A 58-document batch is debugged through error_message. "invalid
    path" would leave an operator guessing; naming the scanner does not."""
    with pytest.raises(ValueError) as excinfo:
        rooted(tmp_path).resolve_document("/data/x.docx")
    message = str(excinfo.value)
    assert "pipeline.scanner" in message
    assert "/data/x.docx" in message


def test_refusing_beats_silently_building_a_wrong_path(tmp_path):
    """The reason this raises rather than joining.

    `Path("D:/root") / "/data/x"` yields `D:/data/x` on Windows and
    `/data/x` on POSIX — a real path, pointing at the wrong place, that
    would fail later as a confusing "file not found" or, worse, succeed
    against an unrelated file.
    """
    naive = pathlib.PurePath(str(tmp_path)) / "/data/x.docx"
    assert str(naive) != str(tmp_path / "data" / "x.docx") or True   # documents the trap
    with pytest.raises(ValueError):
        rooted(tmp_path).resolve_document("/data/x.docx")


# ---------------------------------------------------------------------------
# Settings invariants
# ---------------------------------------------------------------------------

def test_celery_settings_are_always_present():
    """Every call site does `settings.celery.x` unguarded; if this were
    ever optional they would all raise AttributeError instead."""
    assert get_settings().celery is not None
    assert get_settings().celery.broker_url.startswith("redis://")


def test_the_broker_url_carries_credentials_when_a_password_is_set():
    settings = get_settings()
    import os
    if os.environ.get("REDIS_PASSWORD", "").strip():
        assert "@" in settings.celery.broker_url


def test_broker_and_result_backend_use_different_databases():
    """Sharing one Redis database mixes queued messages with stored
    results, and a `FLUSHDB` of one wipes the other."""
    settings = get_settings()
    assert settings.celery.broker_url != settings.celery.result_backend


def test_qdrant_collection_names_are_prefixed_per_project():
    """Isolation is topological — one collection per project (§4.2)."""
    qdrant = get_settings().qdrant
    assert qdrant.collection_for("PRJ-03") == "project_PRJ-03"
    assert qdrant.collection_for("TEST-PROJECT") == "project_TEST-PROJECT"


def test_vector_size_matches_the_embedding_model():
    """1536 is fixed by text-embedding-3-small. Changing it means
    recreating every collection, so it must not drift silently."""
    assert get_settings().qdrant.vector_size == 1536


def test_settings_are_cached_per_process():
    assert get_settings() is get_settings()
