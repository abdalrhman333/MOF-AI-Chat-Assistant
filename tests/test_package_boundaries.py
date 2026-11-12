"""`pipeline/` must not import `Application/`, because the image cannot.

The three packages are separate processes with different database
privileges: `pipeline/` builds the knowledge base as `spm_admin`,
`Application/` serves it as the SELECT-only `spm_app`. The Dockerfile
encodes that — the `pipeline` target copies `pipeline/` and nothing else.

So an import reaching from the pipeline into the application is not a
style question. It is a stage that passes every test on a developer's
machine, where the whole repository is importable, and fails in the
container, where it is not.

That is not hypothetical. `classify.py` imported `build_llm` from
`Application.backend.answer` behind a lazy `if llm is None:` branch. Every
unit test injected a client, so the branch never ran; the daily pipeline
had no unclassified documents, so the stage reported nothing to do. It
surfaced on 2026-11-12, on the first new document since the image was
built:

    "stage": "classify", "ok": false,
    "error": "ModuleNotFoundError: No module named 'Application'"

These tests read the source rather than importing it, so they fail for a
lazy import inside a function body too — which is exactly where the last
one hid.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "pipeline"


def modules(package: pathlib.Path):
    return sorted(p for p in package.glob("*.py") if p.name != "__init__.py")


def imported_names(path: pathlib.Path) -> set[str]:
    """Every module name imported, including inside functions.

    `ast` walks the whole tree, so a deferred import in a branch counts —
    the kind that unit tests with an injected dependency never execute.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", modules(PIPELINE), ids=lambda p: p.name)
def test_no_pipeline_module_imports_the_application(path):
    """⚠️ The image has no `Application/`. An import of it here is a
    container-only failure, invisible to every test that injects."""
    offenders = {n for n in imported_names(path)
                 if n == "Application" or n.startswith("Application.")}
    assert not offenders


@pytest.mark.parametrize("path", modules(PIPELINE), ids=lambda p: p.name)
def test_no_pipeline_module_imports_the_evaluation_harness(path):
    """Same reasoning: `evaluation/` is not in the image either, and the
    thing that judges the knowledge base has no business being imported by
    the thing that builds it."""
    offenders = {n for n in imported_names(path)
                 if n == "evaluation" or n.startswith("evaluation.")}
    assert not offenders


def test_the_pipeline_image_copies_only_the_pipeline():
    """The claim the tests above rest on. If the Dockerfile ever starts
    copying `Application/` into the pipeline target, these tests are
    enforcing a rule that no longer exists and should be revisited
    deliberately rather than left passing by accident."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    target = text.split("FROM base AS pipeline")[1].split("FROM base AS app")[0]
    copied = [line.strip() for line in target.splitlines()
              if line.strip().startswith("COPY")]
    assert copied == ["COPY pipeline/ ./pipeline/"], copied


def test_build_llm_lives_in_the_pipeline_package():
    """Where it has to be for `classify` to construct a client in the
    container. The application re-exports it, so this is not a statement
    about who may *use* it."""
    from pipeline.llm_client import build_llm as from_pipeline

    from Application.backend.answer import build_llm as from_application

    assert from_application is from_pipeline
