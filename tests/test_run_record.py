"""Tests for pipeline/run_record.py — the note each full ingest leaves.

The status check's verdict on the schedule is only as good as this record,
so the properties that matter are: a success is kept separately from the
latest attempt, recording never breaks a run, and a damaged record reads as
"no record" rather than crashing the diagnostic.
"""
from __future__ import annotations

import datetime as dt

from pipeline import run_record


class FakeRedis:
    def __init__(self, broken=False):
        self.data: dict[str, str] = {}
        self.broken = broken

    def set(self, key, value):
        if self.broken:
            raise ConnectionError("redis down")
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)


OK = {"ok": True, "stages": [{"stage": "scan", "ok": True, "written": 0}]}
FAILED = {"ok": False, "stages": [
    {"stage": "scan", "ok": False, "attempts": 4,
     "error": "OperationalError: the database system is starting up " + "x" * 500}]}


def test_a_success_is_both_the_last_run_and_the_last_success():
    redis = FakeRedis()
    run_record.record(OK, client=redis)
    last_run, last_success = run_record.read(client=redis)
    assert last_run["ok"] and last_success["ok"]


def test_a_failure_does_not_overwrite_the_last_success():
    """The useful report on a bad day is both: what failed, and when it last
    worked."""
    redis = FakeRedis()
    earlier = dt.datetime(2026, 9, 13, 10, 0, tzinfo=dt.timezone.utc)
    run_record.record(OK, client=redis, now=earlier)
    run_record.record(FAILED, client=redis)

    last_run, last_success = run_record.read(client=redis)

    assert last_run["ok"] is False
    assert last_success["finished_at"] == earlier


def test_the_record_keeps_why_a_stage_failed_and_how_often_it_tried():
    redis = FakeRedis()
    run_record.record(FAILED, client=redis)
    stage = run_record.read(client=redis)[0]["stages"][0]
    assert "starting up" in stage["error"]
    assert stage["attempts"] == 4
    assert len(stage["error"]) <= 300, "the record is a summary, not the log"


def test_recording_never_fails_the_run():
    """The run did its work; a note that could not be written must not turn
    that into a failure."""
    run_record.record(OK, client=FakeRedis(broken=True))    # must not raise


def test_nothing_recorded_reads_as_none():
    assert run_record.read(client=FakeRedis()) == (None, None)


def test_a_corrupt_record_reads_as_none_rather_than_crashing():
    redis = FakeRedis()
    redis.data[run_record.LAST_RUN_KEY] = "{not json"
    redis.data[run_record.LAST_SUCCESS_KEY] = '{"ok": true}'     # no timestamp
    assert run_record.read(client=redis) == (None, None)


def test_times_are_stored_with_their_timezone():
    """Compared against now(utc) by the status check; a naive timestamp
    would raise, and a local one would be off by hours."""
    redis = FakeRedis()
    run_record.record(OK, client=redis)
    assert run_record.read(client=redis)[0]["finished_at"].tzinfo is not None
