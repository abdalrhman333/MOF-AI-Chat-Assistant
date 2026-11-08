"""`scripts/bootstrap.ps1` is all wiring, which is what survives mutations.

The script's whole value is that it *notices* things: a missing `.env`, a
`DOCUMENT_ROOT_HOST` still pointing at the previous machine's drive, an
empty session secret, an absent licensed font. Every one of those checks is
a few lines that look obviously right and can be deleted without any other
test caring — exactly the failure mode recorded in `STATUS_AND_ROADMAP.md`
§25c, where four of fifteen mutations survived and all four were the wiring
between tested parts.

So these tests run the real script, with `-CheckOnly`, against a throwaway
directory tree built to be wrong in one specific way, and assert it says so.

The tree is a copy, not the repository: the script derives every path from
its own location, so a copied `scripts/bootstrap.ps1` reads the fixture's
`infra/.env` and nothing of the real one. Nothing here starts a container
or touches the real `.env`.

⚠️ The Docker check is deliberately not asserted on. Whether the engine is
running is a property of the machine, not of the script, and pinning it
would make these tests pass or fail for reasons unrelated to the code. The
exit code depends on it too — a stopped engine is itself a FAIL — so these
tests assert on what the script *reports*, not on how it exits.

Skips rather than fails where PowerShell is absent, as `test_frontend.py`
does for node.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bootstrap.ps1"

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None,
                                reason="PowerShell not available on this machine")

# Enough of a .env for every check to pass. Values are obvious fakes.
COMPLETE_ENV = """\
DATA_ROOT={data_root}
DOCUMENT_ROOT={corpus}
DOCUMENT_ROOT_HOST={corpus}
API_SESSION_SECRET=0123456789abcdef
AZURE_OPENAI_LLM_API_KEY=fake
AZURE_OPENAI_EMBEDDING_API_KEY=fake
AZURE_DOCUMENT_INTELLIGENCE_KEY=fake
"""


def _tree(tmp_path: pathlib.Path, *, env: str | None, fonts: int,
          corpus_files: int) -> pathlib.Path:
    """A fake repository root, wrong in whichever way the test needs."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "bootstrap.ps1")
    (tmp_path / "infra").mkdir()

    corpus = tmp_path / "corpus"
    if corpus_files:
        corpus.mkdir()
        for i in range(corpus_files):
            (corpus / f"doc{i}.docx").write_text("x", encoding="utf-8")

    if env is not None:
        text = env.format(data_root=tmp_path / "volumes", corpus=corpus)
        (tmp_path / "infra" / ".env").write_text(text, encoding="utf-8")

    font_dir = tmp_path / "Application" / "frontend" / "assets" / "fonts"
    font_dir.mkdir(parents=True)
    for i in range(fonts):
        (font_dir / f"Font{i}.ttf").write_text("x", encoding="utf-8")
    return tmp_path


def _run(root: pathlib.Path) -> str:
    done = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(root / "scripts" / "bootstrap.ps1"), "-CheckOnly"],
        capture_output=True, text=True, timeout=180,
    )
    return done.stdout + done.stderr


# ---------------------------------------------------------------------------
# It parses at all
# ---------------------------------------------------------------------------

def test_the_script_is_pure_ascii():
    """Windows PowerShell 5.1 reads a .ps1 without a byte-order mark as
    ANSI, so one em dash in a string becomes mojibake and takes the whole
    parse down. This was not hypothetical: the first draft did exactly that
    and could not run at all."""
    SCRIPT.read_bytes().decode("ascii")


def test_the_script_runs_and_reports_rather_than_crashing(tmp_path):
    """Under ErrorActionPreference='Stop', a stopped Docker engine writes to
    stderr and Windows PowerShell turns that into a terminating
    NativeCommandError — so the script died with a stack trace at the exact
    moment its job was to say "the engine is not running". It must always
    reach its own output."""
    out = _run(_tree(tmp_path, env=COMPLETE_ENV, fonts=3, corpus_files=58))
    assert "What the clone does not carry" in out
    assert "NativeCommandError" not in out

    # ⚠️ The line above only catches this when the engine happens to be
    # down, so the decision itself is pinned here. Every native call in the
    # script is checked by its exit code; 'Stop' would add nothing and
    # breaks the reporting path.
    assert "$ErrorActionPreference = 'Continue'" in SCRIPT.read_text(encoding="ascii")
    assert "$ErrorActionPreference = 'Stop'" not in SCRIPT.read_text(encoding="ascii")


# ---------------------------------------------------------------------------
# It notices each of the four uncarried assets
# ---------------------------------------------------------------------------

def test_a_missing_env_file_is_reported(tmp_path):
    out = _run(_tree(tmp_path, env=None, fonts=3, corpus_files=58))
    assert "infra/.env missing" in out


def test_an_empty_session_secret_is_reported(tmp_path):
    """It has no default anywhere, so sign-in refuses with nothing pointing
    at why. The script is the only thing that explains it."""
    env = COMPLETE_ENV.replace("API_SESSION_SECRET=0123456789abcdef",
                              "API_SESSION_SECRET=")
    out = _run(_tree(tmp_path, env=env, fonts=3, corpus_files=58))
    assert "API_SESSION_SECRET" in out
    assert "refuse" in out


def test_a_corpus_path_from_the_old_machine_is_reported(tmp_path):
    """The single likeliest thing to be wrong after a move."""
    env = COMPLETE_ENV.replace("DOCUMENT_ROOT_HOST={corpus}",
                              "DOCUMENT_ROOT_HOST=Z:/nowhere")
    out = _run(_tree(tmp_path, env=env, fonts=3, corpus_files=58))
    assert "does not exist on this machine" in out


def test_a_short_corpus_is_reported(tmp_path):
    """58 is the built corpus. Fewer means a copy that stopped early, which
    otherwise shows up much later as missing citations."""
    out = _run(_tree(tmp_path, env=COMPLETE_ENV, fonts=3, corpus_files=12))
    assert "holds 12 files" in out


def test_a_missing_brand_font_is_reported(tmp_path):
    """Licensed and gitignored, so a clone never has it. Absent, the UI
    falls back to a system face — cosmetic, and easy to ship unnoticed."""
    out = _run(_tree(tmp_path, env=COMPLETE_ENV, fonts=0, corpus_files=58))
    assert "DIN Next LT Arabic missing" in out


def test_an_empty_azure_key_is_reported(tmp_path):
    env = COMPLETE_ENV.replace("AZURE_DOCUMENT_INTELLIGENCE_KEY=fake",
                              "AZURE_DOCUMENT_INTELLIGENCE_KEY=")
    out = _run(_tree(tmp_path, env=env, fonts=3, corpus_files=58))
    assert "AZURE_DOCUMENT_INTELLIGENCE_KEY is empty" in out


def test_a_complete_tree_reports_none_of_those_problems(tmp_path):
    """The mirror of every test above: with all four assets present, the
    script must not invent a problem. Without this, a check hard-coded to
    always fail would pass every other test here."""
    out = _run(_tree(tmp_path, env=COMPLETE_ENV, fonts=3, corpus_files=58))
    for phrase in ("infra/.env missing", "does not exist on this machine",
                   "DIN Next LT Arabic missing", "is empty"):
        assert phrase not in out
    assert "infra/.env present, session secret set" in out


# ---------------------------------------------------------------------------
# It does not spend money
# ---------------------------------------------------------------------------

def test_check_only_starts_nothing(tmp_path):
    """-CheckOnly is what someone runs first, before trusting the script."""
    out = _run(_tree(tmp_path, env=COMPLETE_ENV, fonts=3, corpus_files=58))
    assert "Nothing was started" in out
    assert "docker compose up" not in out


def test_the_script_never_runs_the_billed_stage_itself():
    """Stage 1 is billed per page. The script prints the command and stops;
    an unattended setup script that spends money is a defect, however
    convenient. `ingest` must appear only inside the closing instructions,
    never as something the script invokes."""
    executable = []
    in_here_string = False
    for line in SCRIPT.read_text(encoding="ascii").splitlines():
        if line.strip().endswith("@'"):       # start of a literal here-string
            in_here_string = True
            continue
        if line.strip() == "'@":              # its terminator, at column 0
            in_here_string = False
            continue
        if in_here_string or line.lstrip().startswith("#"):
            continue                          # printed text, or a comment
        executable.append(line)

    code = "\n".join(executable)
    assert "pipeline.tasks ingest" not in code
    assert "pipeline.ocr" not in code
    # The commands must still reach the operator somewhere.
    assert "pipeline.tasks ingest" in SCRIPT.read_text(encoding="ascii")
