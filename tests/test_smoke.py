"""test_smoke.py — stdlib-only packaging smoke test for the draw-cli (draw_cli) package.

Pins the pipx packaging contract: the package imports, exposes a version, the
console entry point `draw_cli.cli:main` resolves to a callable, and the
install-skill invariants (SKILL_NAME / SKILL_BLURB) hold. Deliberately does NOT
import huggingface_hub / Pillow — draw_cli.cli imports those lazily inside
generate(), so this runs with zero runtime deps installed.

These are pytest `test_*` functions (collected by `pytest tests/`) so the CI test
gate exercises the packaging contract, not just the version flags. Run:
    python3 -m pytest tests/ -q       # collected automatically
    python3 tests/test_smoke.py       # direct, no pytest
"""
from __future__ import annotations

import importlib
import os
import sys

# Make the repo root importable regardless of CWD.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_package_imports_and_has_version() -> None:
    import draw_cli

    assert draw_cli.__version__, "draw_cli.__version__ is empty/missing"


def test_cli_main_is_callable() -> None:
    import draw_cli.cli as cli

    assert callable(cli.main), "draw_cli.cli.main is not callable"


def test_install_skill_invariants() -> None:
    # install-skill invariants: the registered name and the always-on blurb.
    import draw_cli.cli as cli

    assert cli.SKILL_NAME == "draw", f"SKILL_NAME != 'draw': {cli.SKILL_NAME!r}"
    assert "draw" in cli.SKILL_BLURB, "SKILL_BLURB does not mention 'draw'"


def test_entry_point_resolves() -> None:
    # Resolve the [project.scripts] entry point string the same way pipx would:
    # "draw_cli.cli:main" -> import module, getattr(attr).
    module_name, attr = "draw_cli.cli:main".split(":", 1)
    entry = getattr(importlib.import_module(module_name), attr)
    assert callable(entry), "entry point draw_cli.cli:main is not callable"


if __name__ == "__main__":
    test_package_imports_and_has_version()
    test_cli_main_is_callable()
    test_install_skill_invariants()
    test_entry_point_resolves()
    print("smoke: OK")
