"""Version-flag tests for the `draw` CLI.

These guard four things:
  * `draw --version` (and `-V`) prints `draw <version>` and exits 0, even though
    `-o/--out` is otherwise required — the version action must short-circuit
    before argparse enforces the required option.
  * The printed version is the SAME string declared in `draw_cli/__init__.py`'s
    `__version__` — the single source of truth — so the CLI output can never
    silently drift from the declared version.
  * `pyproject.toml` declares the version as DYNAMIC and resolves it from
    `draw_cli.__version__` (`[tool.setuptools.dynamic]`), so the dist metadata is
    wired to that same single source and a future static `[project] version` (which
    would reintroduce drift) is caught.
  * The symlink install path (install.sh symlinks bin/draw onto PATH) resolves
    back to the real repo and still prints the version.

`bin/draw` is a thin shim that imports `draw_cli.cli:main`; the version lives in
`draw_cli.__version__` (the single source of truth — pyproject resolves it via a
build-time `attr=` reference). Run: `python3 -m pytest tests/` (or plain
`python3 tests/test_version.py`).
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
INIT_PY = REPO_ROOT / "draw_cli" / "__init__.py"


def _declared_version() -> str:
    """Independently parse `__version__` from draw_cli/__init__.py — do NOT import
    the package, so the test is a genuine cross-check of the single source of truth,
    not a tautology, and an installed-but-stale dist can't mask drift."""
    text = INIT_PY.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"""\s*__version__\s*=\s*['"]([^'"]+)['"]""", line)
        if m:
            return m.group(1)
    raise AssertionError("no __version__ in draw_cli/__init__.py")


def _run_version(flag: str, executable: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, executable or str(DRAW), flag],
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_flag_prints_declared_version() -> None:
    result = _run_version("--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"draw {_declared_version()}"


def test_short_version_flag_is_correct_not_just_equal() -> None:
    # Assert -V on its own merits (exit 0 + exact non-empty string), not merely
    # that it equals --version: two broken flags both printing "" would otherwise
    # pass an equality-only check.
    result = _run_version("-V")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"draw {_declared_version()}"


def test_package_version_matches_declared_version() -> None:
    # Drift guard: the imported package's __version__ (which backs `draw --version`)
    # must equal the version text parsed independently from __init__.py above, so an
    # installed-but-stale dist in the test interpreter can't mask drift.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import draw_cli

    assert draw_cli.__version__ == _declared_version()


def test_pyproject_version_is_dynamic_from_package() -> None:
    # The version must be SINGLE-SOURCED from draw_cli.__version__: pyproject declares
    # it dynamic and resolves it via `attr = "draw_cli.__version__"`. A reintroduced
    # static `[project] version` would silently allow drift again — catch that here.
    text = PYPROJECT.read_text(encoding="utf-8")
    assert re.search(r'^\s*dynamic\s*=\s*\[\s*["\']version["\']\s*\]', text, re.M), (
        "pyproject [project] must declare dynamic = [\"version\"]"
    )
    assert re.search(
        r'attr\s*=\s*["\']draw_cli\.__version__["\']', text
    ), "pyproject must resolve version via attr = \"draw_cli.__version__\""
    # And no static [project] version line that would reintroduce a second source.
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project and re.match(r"""version\s*=\s*['"]""", stripped):
            raise AssertionError("static [project] version reintroduces drift; keep it dynamic")


def test_version_via_symlink_resolves_real_repo() -> None:
    # Regression: draw is installed on PATH as a SYMLINK to bin/draw in the checked-out
    # repo. Invoked that way, a naive __file__ points at the symlink's dir (no draw_cli
    # package there). The shim resolves the symlink (os.path.realpath) back to the repo
    # root and inserts it on sys.path so `from draw_cli.cli import main` succeeds. The
    # other tests run bin/draw by its real path and would not catch this — so exercise
    # the symlinked invocation explicitly.
    with tempfile.TemporaryDirectory() as tmp:
        link = os.path.join(tmp, "draw")
        os.symlink(str(DRAW), link)
        result = _run_version("--version", executable=link)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"draw {_declared_version()}"


def test_version_does_not_match_stale_default() -> None:
    # Drift guard: the version must MOVE off the original 0.1.0 sentinel once a
    # feature ships. If this trips back to 0.1.0 the version regressed.
    assert _declared_version() != "0.1.0"


if __name__ == "__main__":
    test_version_flag_prints_declared_version()
    test_short_version_flag_is_correct_not_just_equal()
    test_package_version_matches_declared_version()
    test_pyproject_version_is_dynamic_from_package()
    test_version_via_symlink_resolves_real_repo()
    test_version_does_not_match_stale_default()
    print("ok: all version tests passed")
