"""Vector search, scoped to one session's project.

**This module takes a `Session`, never a project code.** That is the whole
point of its signature. `Session` is obtainable only from the gate, so a
function shaped this way *cannot* be called with a project the user was
never granted — the AC09 guarantee extends into retrieval by construction
rather than by everyone remembering to check.

There is no shared index to misfilter either: each project has its own
Qdrant collection (`SOLUTION_ARCHITECTURE.md` §4.2), and the collection
name is derived from the session. Two independent mechanisms would have to
fail for a passage from another project to appear here.

**No LLM.** This module retrieves and ranks; `answer.py` decides what to do
with what it finds. Keeping them apart means retrieval can be measured on
its own — which is exactly what `evaluation/` already does — without a
paid completion in the loop.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from Application.backend.errors import BackendUnavailable
from Application.backend.session import Session
from pipeline.config import Settings, get_settings

import observability


@dataclass(frozen=True)
class Passage:
    """One retrieved chunk, with everything a citation needs.

    Every field here comes from the payload the indexer wrote — nothing is
    inferred, and nothing arrives from the model. That matters later:
    `answer.py` lets the LLM nominate *which* passages it used by number,
    but the filename, page and section attached to a citation are always
    read from here.
    """
    rank: int                    # 1-based, as returned
    score: float
    filename: str
    doc_type: str
    page: int | None
    section: str
    text: str

    @property
    def cite(self) -> str:
        """Human-readable source, e.g. `Risk Register — page 3 — Top Risks`."""
        bits = [self.doc_type or self.filename]
        if self.page:
            bits.append(f"page {self.page}")
        if self.section:
            bits.append(self.section)
        return " — ".join(bits)


@dataclass
class RetrievalResult:
    passages: list[Passage]
    embed_ms: int = 0
    search_ms: int = 0

    @property
    def best_score(self) -> float:
        return self.passages[0].score if self.passages else 0.0

    def above(self, floor: float) -> list[Passage]:
        return [p for p in self.passages if p.score >= floor]


def embed_question(question: str, settings: Settings, embedder) -> list[float]:
    """One embedding, the same model the corpus was indexed with.

    Deliberately reuses `pipeline.indexer.build_embedder` rather than
    constructing its own client: a question embedded by a different model
    than the passages would return confident nonsense, with nothing
    anywhere reporting an error.
    """
    response = embedder.embeddings.create(
        model=settings.models.embedding_deployment,
        input=[question],
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    if not ordered:
        raise BackendUnavailable("the embedding service returned nothing")
    return ordered[0].embedding


def search(
    session: Session,
    question: str,
    *,
    limit: int | None = None,
    settings: Settings | None = None,
    embedder=None,
    qdrant=None,
) -> RetrievalResult:
    """Find the passages in *this session's project* most like the question.

    Raises `BackendUnavailable` if the vector store or embedding service
    cannot be reached — never an empty result, because empty is
    indistinguishable from "nothing matched" and would be read as a
    legitimate "not in the documents" answer.
    """
    settings = settings or get_settings()
    limit = limit or settings.answering.top_k

    if not question or not question.strip():
        return RetrievalResult(passages=[])

    embedder = embedder or _build_embedder(settings)
    qdrant = qdrant or _build_qdrant(settings)

    started = time.perf_counter()
    with observability.span("retrieval.embed",
                            **{"gen_ai.request.model":
                               settings.models.embedding_deployment}):
        try:
            vector = embed_question(question.strip(), settings, embedder)
        except BackendUnavailable:
            raise
        except Exception as exc:                            # noqa: BLE001
            raise BackendUnavailable(
                f"could not embed the question: {exc}") from exc
    embed_ms = int((time.perf_counter() - started) * 1000)

    # Derived from the session, never from an argument. This single line is
    # what makes cross-project retrieval structurally impossible here.
    collection = settings.qdrant.collection_for(session.project_code)

    started = time.perf_counter()
    with observability.span("retrieval.search", collection=collection,
                            limit=limit):
        try:
            response = qdrant.query_points(
                collection_name=collection, query=vector,
                limit=limit, with_payload=True,
            )
        except Exception as exc:                            # noqa: BLE001
            raise BackendUnavailable(
                f"could not search the knowledge base for "
                f"{session.project_code}: {exc}"
            ) from exc
    search_ms = int((time.perf_counter() - started) * 1000)

    passages = []
    for index, point in enumerate(response.points):
        payload = point.payload or {}
        passages.append(Passage(
            rank=index + 1,
            score=float(point.score),
            filename=payload.get("filename", ""),
            doc_type=payload.get("doc_type", ""),
            page=payload.get("page"),
            section=payload.get("section", ""),
            text=payload.get("text", ""),
        ))
    return RetrievalResult(passages=passages, embed_ms=embed_ms, search_ms=search_ms)


def _build_embedder(settings: Settings):
    """Cached per settings — see `clients.py` for why that is an AC08 fix.

    Rebuilding these per question cost roughly 1.5 s before the connection
    was even used, and a fresh HTTPS client cannot reuse a connection.
    """
    from Application.backend import clients
    return clients.embedder(settings)


def _build_qdrant(settings: Settings):
    from Application.backend import clients
    return clients.vector_store(settings)
