"""Version-flag tests for the `draw` CLI.

These guard two things:
  * `draw --version` (and `-V`) prints `draw <version>` and exits 0, even though
    `-o/--out` is otherwise required — the version action must short-circuit.
  * The printed version is the SAME string declared in `pyproject.toml`'s
    `[project] version`, so the dynamic resolver can never silently drift from
    the single source of truth.

Run: `python3 -m pytest tests/` (or plain `python3 tests/test_version.py`).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DRAW = REPO_ROOT / "bin" / "draw"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _pyproject_version() -> str:
    """Independently parse `[project] version` — do NOT reuse the CLI's own
    parser, so the test is a genuine cross-check, not a tautology."""
    text = PYPROJECT.read_text(encoding="utf-8")
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = re.match(r"""version\s*=\s*['"]([^'"]+)['"]""", stripped)
            if m:
                return m.group(1)
    raise AssertionError("no [project] version in pyproject.toml")


def _run_version(flag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DRAW), flag],
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_flag_prints_pyproject_version() -> None:
    result = _run_version("--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"draw {_pyproject_version()}"


def test_short_version_flag_is_correct_not_just_equal() -> None:
    # Assert -V on its own merits (exit 0 + exact non-empty string), not merely
    # that it equals --version: two broken flags both printing "" would otherwise
    # pass an equality-only check.
    result = _run_version("-V")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"draw {_pyproject_version()}"


def test_pyproject_fallback_resolver_matches_declared_version() -> None:
    # Exercise the source-checkout resolver path directly so the drift guard holds
    # regardless of whether draw-cli happens to be installed as a dist in the test
    # interpreter (an installed-but-stale dist would otherwise mask drift). This is
    # the path that backs `draw --version` in a symlinked checkout, the common case.
    src = DRAW.read_text(encoding="utf-8")
    ns: dict[str, object] = {"__file__": str(DRAW)}
    exec(compile(src, str(DRAW), "exec"), ns)  # noqa: S102 - trusted in-repo source
    resolver = ns["_version_from_pyproject"]
    assert callable(resolver)
    assert resolver() == _pyproject_version()


def test_version_via_symlink_resolves_real_repo() -> None:
    # Regression: draw is installed on PATH as a SYMLINK to bin/draw in the checked-out
    # repo. Invoked that way, a naive __file__ points at the symlink's dir (no pyproject
    # there) and --version printed "unknown". Resolving the symlink (os.path.realpath)
    # must land back at the repo root. The other tests run bin/draw by its real path and
    # would not catch this — so exercise the symlinked invocation explicitly.
    with tempfile.TemporaryDirectory() as tmp:
        link = os.path.join(tmp, "draw")
        os.symlink(str(DRAW), link)
        result = subprocess.run(
            [sys.executable, link, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"draw {_pyproject_version()}"


def test_version_does_not_match_stale_default() -> None:
    # Drift guard: the version must MOVE off the original 0.1.0 sentinel once a
    # feature ships (the ship.sh bump gate enforces the same on release). If this
    # trips back to 0.1.0 the resolver or pyproject regressed.
    assert _pyproject_version() != "0.1.0"


if __name__ == "__main__":
    test_version_flag_prints_pyproject_version()
    test_short_version_flag_is_correct_not_just_equal()
    test_pyproject_fallback_resolver_matches_declared_version()
    test_version_via_symlink_resolves_real_repo()
    test_version_does_not_match_stale_default()
    print("ok: all version tests passed")
