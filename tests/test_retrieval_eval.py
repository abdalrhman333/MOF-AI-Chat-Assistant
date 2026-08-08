"""Tests for the retrieval evaluation harness.

A measurement tool that is wrong is worse than no measurement, because its
output is a number and numbers get believed. Three classes of wrongness are
possible here and all three are silent:

  1. **The arithmetic is wrong.** recall@3 counting a rank-4 hit, or MRR
     averaging over the wrong denominator, would make every future
     comparison meaningless while looking entirely plausible.
  2. **The questions are scored against the wrong results.** The embedding
     API returns an explicit index; trusting arrival order instead would
     pair each question with another question's search results. The indexer
     already learned this — same bug, quieter consequence.
  3. **The reference set has drifted from the corpus.** A renamed document
     scores zero forever and reads exactly like a retrieval regression.

Nothing here calls a paid API or touches the live vector store: the
embedder and Qdrant are stubbed, and the corpus check is fed a fake cursor.
"""
from __future__ import annotations

import json

import pytest

from evaluation import dataset as ds
from evaluation import retrieval_eval as ev
from pipeline.config import get_settings


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class StubEmbedding:
    def __init__(self, index, vector):
        self.index, self.embedding = index, vector


class StubEmbeddingResponse:
    def __init__(self, data):
        self.data = data


class StubEmbedder:
    """Returns one vector per input. `shuffle` reproduces an API that
    answers out of order — which is legal, and which arrival-order code
    would silently mis-pair."""

    def __init__(self, shuffle=False, drop=0):
        self.shuffle, self.drop = shuffle, drop
        self.calls = []
        self.embeddings = self

    def create(self, model, input):                       # noqa: A002
        self.calls.append(list(input))
        items = [StubEmbedding(i, [float(i), 0.0]) for i in range(len(input))]
        if self.drop:
            items = items[: -self.drop]
        if self.shuffle:
            items = list(reversed(items))
        return StubEmbeddingResponse(items)


class ExplodingEmbedder:
    def __init__(self):
        self.embeddings = self

    def create(self, model, input):                       # noqa: A002
        raise AssertionError("a dry run must not embed anything")


class StubPoint:
    def __init__(self, filename, score, section=""):
        self.score = score
        self.payload = {"filename": filename, "section": section}


class StubResponse:
    def __init__(self, points):
        self.points = points


class StubQdrant:
    """Maps collection name -> ordered filenames it returns."""

    def __init__(self, results: dict[str, list[str]], fail: set[str] | None = None):
        self.results, self.fail = results, fail or set()
        self.queried = []

    def query_points(self, collection_name, query, limit, with_payload=True):
        self.queried.append(collection_name)
        if collection_name in self.fail:
            raise ConnectionError("qdrant is down")
        names = self.results.get(collection_name, [])[:limit]
        return StubResponse([
            StubPoint(n, round(0.9 - 0.05 * i, 3)) for i, n in enumerate(names)
        ])


class StubCursor:
    def __init__(self, projects, documents):
        self.projects, self.documents = projects, documents
        self._rows = []

    def execute(self, sql, params=None):
        self._rows = (
            [(p,) for p in self.projects] if "FROM projects" in sql
            else list(self.documents)
        )
        return self

    def fetchall(self):
        return self._rows


class StubConn:
    def __init__(self, projects, documents):
        self._cursor = StubCursor(projects, documents)

    def cursor(self):
        return self._cursor


def write_set(tmp_path, questions, name="q.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"questions": questions}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def q(qid="q1", project="PRJ-01", lang="en", text="what?", expect=("a.docx",)):
    return {"id": qid, "project": project, "lang": lang,
            "question": text, "expect_documents": list(expect)}


# ---------------------------------------------------------------------------
# Loading the set
# ---------------------------------------------------------------------------

def test_a_valid_set_loads(tmp_path):
    data = ds.load(write_set(tmp_path, [q(), q("q2", lang="ar")]))
    assert [x.id for x in data.questions] == ["q1", "q2"]
    assert data.projects == ["PRJ-01"]


def test_a_missing_file_says_how_to_point_at_one(tmp_path):
    with pytest.raises(ds.DatasetError) as exc:
        ds.load(tmp_path / "nope.json")
    assert "EVAL_QUESTIONS_PATH" in str(exc.value)


def test_invalid_json_is_reported_as_such(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ds.DatasetError, match="not valid JSON"):
        ds.load(path)


def test_an_empty_set_is_refused(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text('{"questions": []}', encoding="utf-8")
    with pytest.raises(ds.DatasetError, match="no 'questions'"):
        ds.load(path)


def test_a_duplicate_id_is_refused(tmp_path):
    """Two questions sharing an id makes per-question results ambiguous."""
    with pytest.raises(ds.DatasetError, match="duplicate"):
        ds.load(write_set(tmp_path, [q("same"), q("same")]))


def test_an_unknown_language_is_refused(tmp_path):
    """A typo would create a silent fourth bucket and hide the ar/en gap
    the breakdown exists to show."""
    with pytest.raises(ds.DatasetError, match="lang"):
        ds.load(write_set(tmp_path, [q(lang="arabic")]))


def test_a_missing_field_names_the_question(tmp_path):
    entry = q("q7")
    del entry["expect_documents"]
    with pytest.raises(ds.DatasetError) as exc:
        ds.load(write_set(tmp_path, [entry]))
    assert "q7" in str(exc.value) and "expect_documents" in str(exc.value)


def test_expect_documents_must_be_a_list_of_names(tmp_path):
    entry = q("q8")
    entry["expect_documents"] = "a.docx"
    with pytest.raises(ds.DatasetError, match="list of filenames"):
        ds.load(write_set(tmp_path, [entry]))


# ---------------------------------------------------------------------------
# Agreement with the corpus — the drift that reads as a regression
# ---------------------------------------------------------------------------

def test_a_set_matching_the_corpus_reports_no_problems(tmp_path):
    data = ds.load(write_set(tmp_path, [q()]))
    conn = StubConn(["PRJ-01"], [("PRJ-01", "a.docx")])
    assert ds.validate_against_corpus(data, conn) == []


def test_an_unknown_project_is_reported(tmp_path):
    data = ds.load(write_set(tmp_path, [q(project="PRJ-99")]))
    conn = StubConn(["PRJ-01"], [("PRJ-01", "a.docx")])
    problems = ds.validate_against_corpus(data, conn)
    assert len(problems) == 1 and "PRJ-99" in problems[0]


def test_a_renamed_document_is_reported(tmp_path):
    """The exact failure this check exists for: the file was renamed, the
    question still names the old one, and it would score zero forever."""
    data = ds.load(write_set(tmp_path, [q(expect=("old_name.docx",))]))
    conn = StubConn(["PRJ-01"], [("PRJ-01", "new_name.docx")])
    problems = ds.validate_against_corpus(data, conn)
    assert len(problems) == 1 and "old_name.docx" in problems[0]


def test_every_problem_is_reported_not_just_the_first(tmp_path):
    data = ds.load(write_set(tmp_path, [
        q("a", expect=("gone1.docx",)), q("b", expect=("gone2.docx",))]))
    conn = StubConn(["PRJ-01"], [("PRJ-01", "real.docx")])
    assert len(ds.validate_against_corpus(data, conn)) == 2


def test_a_run_refuses_to_score_against_a_stale_set(tmp_path):
    data = ds.load(write_set(tmp_path, [q(expect=("gone.docx",))]))
    conn = StubConn(["PRJ-01"], [("PRJ-01", "real.docx")])
    with pytest.raises(ValueError, match="disagrees with the corpus"):
        ev.run(dataset=data, connection=conn, settings=get_settings())


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------

def outcome(expect, returned, lang="en", qid="q"):
    question = ds.Question(id=qid, project="P", lang=lang, text="t",
                           expect_documents=tuple(expect))
    hits = [ev.Hit(rank=i + 1, filename=n, score=1.0 - i * 0.1, section="")
            for i, n in enumerate(returned)]
    return ev.Outcome(question=question, results=hits)


def test_rank_is_the_position_of_the_first_expected_document():
    assert outcome(["b.docx"], ["a.docx", "b.docx", "c.docx"]).rank == 2


def test_rank_is_none_when_no_expected_document_came_back():
    o = outcome(["z.docx"], ["a.docx", "b.docx"])
    assert o.rank is None and o.reciprocal_rank == 0.0


def test_any_one_of_several_expected_documents_counts():
    """A budget figure lives in both the charter and the budget report;
    demanding a specific one would measure tie-breaks, not retrieval."""
    assert outcome(["x.docx", "b.docx"], ["a.docx", "b.docx"]).rank == 2


def test_the_earliest_expected_document_wins_when_several_match():
    assert outcome(["b.docx", "c.docx"], ["a.docx", "b.docx", "c.docx"]).rank == 2


@pytest.mark.parametrize("rank_position, k, expected", [
    (1, 1, True), (2, 1, False), (3, 3, True), (4, 3, False), (5, 5, True),
])
def test_recall_cutoffs_are_inclusive_at_the_boundary(rank_position, k, expected):
    """Off-by-one here would silently shift every number ever reported."""
    returned = [f"other{i}.docx" for i in range(rank_position - 1)] + ["hit.docx"]
    assert outcome(["hit.docx"], returned).hit_at(k) is expected


def test_mrr_is_the_mean_of_the_reciprocal_ranks():
    report = ev.Report(outcomes=[
        outcome(["h.docx"], ["h.docx"]),                       # rank 1 -> 1.0
        outcome(["h.docx"], ["a.docx", "h.docx"]),             # rank 2 -> 0.5
        outcome(["h.docx"], ["a.docx", "b.docx", "c.docx"]),   # miss   -> 0.0
    ])
    assert report.mrr() == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert report.recall(1) == pytest.approx(1 / 3)
    assert report.recall(3) == pytest.approx(2 / 3)


def test_mrr_separates_ranking_from_retrieval():
    """The distinction the whole harness exists for: recall@5 identical,
    ranking completely different."""
    third = ev.Report(outcomes=[outcome(["h.docx"], ["a.docx", "b.docx", "h.docx"])])
    first = ev.Report(outcomes=[outcome(["h.docx"], ["h.docx", "a.docx", "b.docx"])])
    assert third.recall(5) == first.recall(5) == 1.0
    assert third.mrr() == pytest.approx(1 / 3)
    assert first.mrr() == 1.0


def test_metrics_over_an_empty_pool_are_zero_not_a_crash():
    empty = ev.Report(outcomes=[])
    assert empty.mrr() == 0.0 and empty.recall(1) == 0.0


def test_errored_questions_are_excluded_from_the_score():
    """Scoring a failed search as a miss would blame retrieval for an
    outage and quietly depress the baseline."""
    broken = outcome(["h.docx"], [])
    broken.error = "ConnectionError: down"
    report = ev.Report(outcomes=[outcome(["h.docx"], ["h.docx"]), broken])
    assert len(report.scored) == 1 and len(report.errors) == 1
    assert report.mrr() == 1.0


def test_language_breakdown_groups_every_scored_question():
    report = ev.Report(outcomes=[
        outcome(["h.docx"], ["h.docx"], lang="ar", qid="a"),
        outcome(["h.docx"], ["x.docx", "h.docx"], lang="ar", qid="b"),
        outcome(["h.docx"], ["h.docx"], lang="en", qid="c"),
    ])
    groups = report.by_language()
    assert {k: len(v) for k, v in groups.items()} == {"ar": 2, "en": 1}
    assert report.mrr(groups["ar"]) == pytest.approx(0.75)
    assert report.mrr(groups["en"]) == 1.0


# ---------------------------------------------------------------------------
# Embedding — the misalignment bug, ported from the indexer
# ---------------------------------------------------------------------------

def test_embeddings_are_re_sorted_by_the_api_index(tmp_path):
    """An out-of-order response must not pair a question with another
    question's vector — which would score every question against the wrong
    search results, with nothing reporting it."""
    data = ds.load(write_set(tmp_path, [q("a", text="first"), q("b", text="second")]))
    ordered = ev.embed_questions(data.questions, get_settings(), StubEmbedder())
    shuffled = ev.embed_questions(data.questions, get_settings(),
                                  StubEmbedder(shuffle=True))
    assert ordered == shuffled == [[0.0, 0.0], [1.0, 0.0]]


def test_a_short_embedding_response_is_an_error(tmp_path):
    data = ds.load(write_set(tmp_path, [q("a"), q("b")]))
    with pytest.raises(RuntimeError, match="count mismatch"):
        ev.embed_questions(data.questions, get_settings(), StubEmbedder(drop=1))


# ---------------------------------------------------------------------------
# End to end, against stubs
# ---------------------------------------------------------------------------

def full_run(tmp_path, questions, corpus_docs, results, **kwargs):
    data = ds.load(write_set(tmp_path, questions))
    conn = StubConn(sorted({d[0] for d in corpus_docs}), corpus_docs)
    return ev.run(dataset=data, connection=conn, settings=get_settings(),
                  embedder=StubEmbedder(), qdrant=StubQdrant(results), **kwargs)


def test_a_full_run_scores_every_question(tmp_path):
    report = full_run(
        tmp_path,
        [q("a", expect=("hit.docx",)), q("b", expect=("miss.docx",))],
        [("PRJ-01", "hit.docx"), ("PRJ-01", "miss.docx")],
        {"project_PRJ-01": ["other.docx", "hit.docx"]},
    )
    assert len(report.scored) == 2
    assert report.outcomes[0].rank == 2
    assert report.outcomes[1].rank is None
    assert report.recall(3) == pytest.approx(0.5)


def test_the_search_stays_inside_the_question_s_own_project(tmp_path):
    """AC09 applies to the harness too — there is no shared index, and the
    collection name must come from settings rather than being assembled."""
    data = ds.load(write_set(tmp_path, [q("a", project="PRJ-05")]))
    conn = StubConn(["PRJ-05"], [("PRJ-05", "a.docx")])
    qdrant = StubQdrant({"project_PRJ-05": ["a.docx"]})
    ev.run(dataset=data, connection=conn, settings=get_settings(),
           embedder=StubEmbedder(), qdrant=qdrant)
    assert qdrant.queried == ["project_PRJ-05"]


def test_a_failed_search_is_recorded_per_question_not_fatal(tmp_path):
    data = ds.load(write_set(tmp_path, [q("a"), q("b", project="PRJ-02")]))
    conn = StubConn(["PRJ-01", "PRJ-02"], [("PRJ-01", "a.docx"), ("PRJ-02", "a.docx")])
    qdrant = StubQdrant({"project_PRJ-01": ["a.docx"]}, fail={"project_PRJ-02"})
    report = ev.run(dataset=data, connection=conn, settings=get_settings(),
                    embedder=StubEmbedder(), qdrant=qdrant)
    assert len(report.scored) == 1 and len(report.errors) == 1
    assert "ConnectionError" in report.errors[0].error


def test_a_dry_run_embeds_nothing(tmp_path):
    """The only paid part of this tool. A dry run must still validate."""
    data = ds.load(write_set(tmp_path, [q()]))
    conn = StubConn(["PRJ-01"], [("PRJ-01", "a.docx")])
    report = ev.run(dataset=data, connection=conn, settings=get_settings(),
                    dry_run=True, embedder=ExplodingEmbedder(), qdrant=None)
    assert report.dry_run and report.questions_planned == 1 and not report.outcomes


def test_search_depth_defaults_to_the_deepest_recall_cutoff(tmp_path):
    """A shallower search would report a recall the query never had the
    chance to achieve."""
    report = full_run(tmp_path, [q()], [("PRJ-01", "a.docx")],
                      {"project_PRJ-01": ["a.docx"]})
    assert report.search_limit == max(get_settings().evaluation.recall_at)


def test_filtering_by_project_and_language(tmp_path):
    questions = [q("a", project="PRJ-01", lang="ar"),
                 q("b", project="PRJ-02", lang="en")]
    docs = [("PRJ-01", "a.docx"), ("PRJ-02", "a.docx")]
    results = {"project_PRJ-01": ["a.docx"], "project_PRJ-02": ["a.docx"]}
    assert full_run(tmp_path, questions, docs, results,
                    project="PRJ-01").questions_planned == 1
    assert full_run(tmp_path, questions, docs, results,
                    lang="en").questions_planned == 1


def test_a_filter_matching_nothing_is_an_error_not_a_perfect_score(tmp_path):
    """An empty pool would otherwise report MRR 0.000 over zero questions,
    which looks like a catastrophic regression."""
    data = ds.load(write_set(tmp_path, [q(project="PRJ-01")]))
    conn = StubConn(["PRJ-01"], [("PRJ-01", "a.docx")])
    with pytest.raises(ValueError, match="no questions match"):
        ev.run(dataset=data, connection=conn, settings=get_settings(),
               project="PRJ-99", embedder=StubEmbedder(), qdrant=StubQdrant({}))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_the_report_names_the_documents_a_miss_returned_instead(tmp_path):
    """A miss is only actionable if you can see what came back instead."""
    report = full_run(tmp_path, [q("a", expect=("wanted.docx",))],
                      [("PRJ-01", "wanted.docx")],
                      {"project_PRJ-01": ["wrong.docx"]})
    text = ev.format_report(report)
    assert "wanted.docx" in text and "wrong.docx" in text


def test_the_json_shape_carries_per_question_ranks(tmp_path):
    report = full_run(tmp_path, [q("a", expect=("hit.docx",))],
                      [("PRJ-01", "hit.docx")],
                      {"project_PRJ-01": ["x.docx", "hit.docx"]})
    payload = ev.to_dict(report)
    assert payload["results"][0]["rank"] == 2
    assert payload["mrr"] == pytest.approx(0.5)
    assert set(payload["recall"]) == {str(k) for k in report.recall_at}


# ---------------------------------------------------------------------------
# The shipped set
# ---------------------------------------------------------------------------

def test_the_shipped_question_set_is_structurally_valid():
    """Guards the file itself — a JSON typo would otherwise only surface
    when someone tried to measure something."""
    data = ds.load(get_settings().evaluation.questions_path)
    assert len(data.questions) >= 20, "too small to be a baseline"
    langs = {q.lang for q in data.questions}
    assert {"ar", "en"} <= langs, "must cover both languages to show the gap"
    assert len(data.projects) >= 5, "a single project would measure one collection"
