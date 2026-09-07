"""Run the frontend suite as part of the one test command.

The frontend tests are JavaScript, because the thing under test is
JavaScript and testing it through a Python approximation would be testing
the approximation. But a suite nobody runs is a suite that rots, so `pytest`
runs them too — one command still covers the whole system.

⚠️ **Skipped, not failed, when node is missing.** The backend does not need
node to build, run or deploy; making it a hard requirement of the test run
would add a dependency that nothing else in the project has. The skip is
loud enough to notice and the CI image has node.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SUITE = Path(__file__).parent / "frontend" / "ui.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not installed; the frontend suite needs it")
def test_the_frontend_suite_passes():
    result = subprocess.run(
        ["node", "--test", str(SUITE)],
        cwd=SUITE.parents[2], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_frontend_suite_is_where_pytest_thinks_it_is():
    """Otherwise the test above would pass by never running anything —
    node exits 0 on an empty run."""
    assert SUITE.is_file()
