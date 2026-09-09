"""Opt-in real Codex acceptance tests.

Never enabled by repository CI. The preflight test does not generate an image.
The generation test can consume the signed-in ChatGPT plan allowance and therefore
requires its own stronger opt-in variable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from draw_cli import codex  # noqa: E402


@pytest.mark.skipif(
    os.environ.get("DRAW_LIVE_CODEX") != "1",
    reason="set DRAW_LIVE_CODEX=1 to exercise the real local ChatGPT-authenticated Codex CLI",
)
def test_live_codex_preflight_only():
    installation = codex.inspect_codex(os.environ.get("DRAW_CODEX_BIN", "codex"))
    assert installation.binary
    assert installation.version
    assert installation.env["CODEX_HOME"]


@pytest.mark.skipif(
    os.environ.get("DRAW_LIVE_CODEX_GENERATE") != "1",
    reason="set DRAW_LIVE_CODEX_GENERATE=1 explicitly; this may consume ChatGPT plan usage",
)
def test_live_native_image_generation(tmp_path):
    output = tmp_path / "live-codex-image.png"
    codex.generate(
        "A plain white square centered on a plain black background, no text.",
        str(output),
        binary=os.environ.get("DRAW_CODEX_BIN", "codex"),
        timeout=900,
    )
    assert output.is_file() and 0 < output.stat().st_size <= codex.MAX_IMAGE_BYTES
    with Image.open(output) as image:
        image.verify()
        assert image.format == "PNG"
