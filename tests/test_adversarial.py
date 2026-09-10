"""Adversarial regression tests added RED-first before the implementation fixes.

These cover failure ordering, provider-specific CLI arguments, oversized prompts,
and thread-scoped artifact discovery. They intentionally describe the desired
contract rather than the current implementation.
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from draw_cli import cli, codex  # noqa: E402

THREAD = "01993034-3060-7000-8000-0123456789ab"
MAX_PROMPT_BYTES = 1024 * 1024


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_invalid_reference_is_rejected_before_codex_preflight(tmp_path):
    """Bad local input must fail before touching/login-checking Codex."""
    bad = tmp_path / "bad.png"
    bad.write_text("not an image", encoding="utf-8")
    with pytest.raises(codex.CodexError, match="invalid image"):
        codex.generate(
            "edit this",
            str(tmp_path / "out.png"),
            binary="/definitely/missing/codex",
            references=[str(bad)],
        )


def test_oversized_prompt_is_rejected_before_codex_preflight(tmp_path):
    """An agent cannot feed an unbounded prompt into a child process."""
    prompt = "x" * (MAX_PROMPT_BYTES + 1)
    with pytest.raises(codex.CodexError, match="prompt.*too large"):
        codex.generate(
            prompt,
            str(tmp_path / "out.png"),
            binary="/definitely/missing/codex",
        )


@pytest.mark.parametrize(
    "extra",
    [
        ["--codex-bin", "/tmp/codex"],
        ["--codex-model", "agent-model"],
    ],
)
def test_hf_rejects_explicit_codex_only_flags(monkeypatch, extra):
    """Provider-specific options must never be silently ignored."""
    monkeypatch.setattr(cli, "generate", lambda *args, **kwargs: pytest.fail("HF generation should not start"))
    monkeypatch.setattr(sys, "argv", ["draw", "--backend", "hf", *extra, "cat", "-o", "out.png"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_hf_accepts_shared_timeout_flag(monkeypatch):
    """--timeout is shared with the API backends, not codex-only; it must be forwarded, not rejected."""
    captured = []
    monkeypatch.setattr(cli, "generate", lambda *args, **kwargs: captured.append(kwargs))
    monkeypatch.setattr(sys, "argv", ["draw", "--backend", "hf", "--timeout", "1", "cat", "-o", "out.png"])
    assert cli.main() == 0
    assert captured[0]["timeout"] == 1.0


@pytest.mark.parametrize("extra", [["--codex-model", "agent-model"], ["--timeout", "1"]])
def test_check_rejects_generation_only_flags(monkeypatch, extra):
    """--check must not pretend that ignored generation settings were checked."""
    monkeypatch.setattr(sys, "argv", ["draw", "--backend", "chatgpt", "--check", *extra])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_thread_scoped_artifact_does_not_depend_on_wall_clock(tmp_path):
    """The UUID namespace is stronger than a mutable filesystem timestamp.

    Wall-clock adjustments, restored metadata, or filesystems with coarse mtimes
    must not make a valid artifact from the current thread disappear.
    """
    home = tmp_path / "codex-home"
    workspace = tmp_path / "workspace"
    root = home / "generated_images" / THREAD
    root.mkdir(parents=True)
    workspace.mkdir()
    expected = _png()
    image = root / "call.png"
    image.write_bytes(expected)
    os.utime(image, (1, 1))

    assert codex._read_artifact(home, workspace, THREAD, time.time()) == expected


def test_diagnostic_redacts_assignment_style_tokens():
    text = codex._diagnostic(
        "OPENAI_API_KEY=super-secret api_key=another-secret access_token=third-secret"
    )
    assert "super-secret" not in text
    assert "another-secret" not in text
    assert "third-secret" not in text
