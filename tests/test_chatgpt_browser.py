"""Offline contract tests for the chatgpt-browser backend: a real fake companion process, no browser."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from draw_cli import chatgpt_browser as cb  # noqa: E402
from draw_cli import cli  # noqa: E402

FAKE = r'''
import json, os, sys
from pathlib import Path
from PIL import Image
mode = os.environ.get("FAKE_MODE", "ok")
args = sys.argv[1:]
log = Path(os.environ["FAKE_LOG"])
FAIL = {"login": ("login_required", 3, {}), "quota": ("quota_exhausted", 4, {"retryAfterMs": 9000000})}
if args[:1] == ["status"]:
    state = "ready" if mode == "ok" else "login_required"
    print(json.dumps({"backends": [{"id": "chatgpt-browser", "state": state, "message": "m",
                                   "steps": ["hsw-editor draw login chatgpt"] if state != "ready" else []}]}))
    raise SystemExit(0 if state == "ready" else 1)
req = json.loads(sys.stdin.read())
log.write_text(json.dumps({"argv": args, "req": req}))
if mode in FAIL:
    code, exit_code, extra = FAIL[mode]
    print(json.dumps({"ok": False, "backend": "chatgpt-browser",
                      "error": {"code": code, "message": "m", "retryable": False, **extra}}))
    raise SystemExit(exit_code)
if mode == "mismatch":
    print(json.dumps({"ok": False, "backend": "chatgpt-browser", "error": {"code": "login_required", "message": "x"}}))
    raise SystemExit(0)
if mode == "noimage":
    print(json.dumps({"ok": True, "backend": "chatgpt-browser", "images": [{}]}))
    raise SystemExit(0)
if mode == "pretty":
    out = Path(req["outDir"]) / "gen.png"
    Image.new("RGB", (64, 64), "white").save(out)
    print(json.dumps({"ok": True, "backend": "chatgpt-browser", "images": [{"path": str(out)}]}, indent=2))
    raise SystemExit(0)
if mode == "hang":
    import time
    time.sleep(60)
if mode == "escape":
    print(json.dumps({"ok": True, "backend": "chatgpt-browser", "images": [{"path": "/etc/hostname"}]}))
    raise SystemExit(0)
out = Path(req["outDir"]) / "gen.png"
Image.new("RGB", (64, 64), "white").save(out)
print(json.dumps({"ok": True, "backend": "chatgpt-browser", "images": [{"path": str(out)}]}))
'''


@pytest.fixture
def companion(tmp_path, monkeypatch):
    script = tmp_path / "fake.py"
    script.write_text(FAKE)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("DRAW_CHATGPT_BROWSER_CMD", f"{sys.executable} {script}")
    return log


def ref(tmp_path, name="ref.png"):
    path = tmp_path / name
    Image.new("RGB", (32, 32), "red").save(path)
    return str(path)


def test_success_sends_contract_request_and_saves_output(companion, tmp_path):
    out = tmp_path / "out.png"
    cb.generate("side view", str(out), references=[ref(tmp_path), ref(tmp_path, "b.png")])
    sent = json.loads(companion.read_text())
    assert sent["argv"] == ["run", "--backend", "chatgpt-browser"]
    request = sent["req"]
    assert request["version"] == 1 and request["n"] == 1
    assert [r.get("role") for r in request["refs"]] == ["subject", None]
    assert Image.open(out).size == (64, 64)


@pytest.mark.parametrize("mode,fragment", [
    ("login", "hsw-editor draw login chatgpt"),
    ("quota", "about 150 min"),
])
def test_failure_classes_become_actionable_errors(companion, tmp_path, monkeypatch, mode, fragment):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(cb.BrowserError) as raised:
        cb.generate("x", str(tmp_path / "o.png"))
    assert fragment in str(raised.value)
    assert not (tmp_path / "o.png").exists()


def test_exit_code_must_agree_with_result(companion, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "mismatch")
    with pytest.raises(cb.BrowserError, match="disagrees"):
        cb.generate("x", str(tmp_path / "o.png"))


def test_image_outside_work_dir_is_refused(companion, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "escape")
    with pytest.raises(cb.BrowserError, match="outside the work directory"):
        cb.generate("x", str(tmp_path / "o.png"))


def test_request_validation():
    with pytest.raises(cb.BrowserError, match="at most 5"):
        cb.build_request("x", ["a"] * 6, "/tmp")
    with pytest.raises(cb.BrowserError, match="not found"):
        cb.build_request("x", ["/nonexistent.png"], "/tmp")
    with pytest.raises(cb.BrowserError, match="prompt is required"):
        cb.build_request("  ", [], "/tmp")


def test_missing_companion_names_the_fix(monkeypatch, tmp_path):
    monkeypatch.setenv("DRAW_CHATGPT_BROWSER_CMD", "/nonexistent/hsw-editor draw")
    with pytest.raises(cb.BrowserError, match="DRAW_CHATGPT_BROWSER_CMD"):
        cb.generate("x", str(tmp_path / "o.png"))


def test_check_reports_state_without_generating(companion, monkeypatch):
    assert "ready" in cb.inspect()
    monkeypatch.setenv("FAKE_MODE", "login")
    with pytest.raises(cb.BrowserError, match="login_required.*hsw-editor draw login chatgpt"):
        cb.inspect()
    assert not companion.exists()


def test_cli_end_to_end(companion, tmp_path, monkeypatch):
    out = tmp_path / "cli.png"
    monkeypatch.setattr(sys, "argv", ["draw", "side view", "--backend", "chatgpt-browser",
                                      "-i", ref(tmp_path), "-o", str(out)])
    assert cli.main() == 0
    assert out.exists()
    monkeypatch.setenv("FAKE_MODE", "login")
    monkeypatch.setattr(sys, "argv", ["draw", "side view", "--backend", "chatgpt-browser",
                                      "-o", str(tmp_path / "x.png")])
    assert cli.main() == 1


def test_cli_rejects_codex_and_model_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["draw", "p", "--backend", "chatgpt-browser", "--model", "sd3.5",
                                      "-o", str(tmp_path / "o.png")])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 2


def test_malformed_success_payload_is_a_clean_error(companion, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "noimage")
    with pytest.raises(cb.BrowserError, match="without a usable image"):
        cb.generate("x", str(tmp_path / "o.png"))


def test_pretty_printed_result_document_is_accepted(companion, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "pretty")
    out = tmp_path / "o.png"
    cb.generate("x", str(out))
    assert out.exists()


def test_timeout_is_forwarded_clamped_and_hung_companion_is_stopped(companion, tmp_path, monkeypatch):
    out = tmp_path / "o.png"
    cb.generate("x", str(out), timeout=1)
    assert json.loads(companion.read_text())["req"]["timeoutMs"] == 5000  # clamped to the contract minimum
    monkeypatch.setenv("FAKE_MODE", "hang")
    with pytest.raises(cb.BrowserError, match="timed out") as raised:
        cb._run(cb.command_argv() + ["run", "--backend", "chatgpt-browser"], "{}", 1)
    assert raised.value.code == "timeout"


def test_bad_command_quoting_is_a_clean_error(monkeypatch):
    monkeypatch.setenv("DRAW_CHATGPT_BROWSER_CMD", "hsw-editor 'unclosed")
    with pytest.raises(cb.BrowserError, match="quoting"):
        cb.command_argv()


def test_cli_reads_prompt_from_stdin_and_rejects_blank(companion, tmp_path, monkeypatch):
    import io
    out = tmp_path / "stdin.png"
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin\n"))
    monkeypatch.setattr(sys, "argv", ["draw", "--backend", "chatgpt-browser", "-o", str(out)])
    assert cli.main() == 0
    assert json.loads(companion.read_text())["req"]["prompt"] == "from stdin"
    monkeypatch.setattr(sys, "argv", ["draw", "   ", "--backend", "chatgpt-browser", "-o", str(out)])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 2


def test_non_dict_error_is_a_clean_error():
    with pytest.raises(cb.BrowserError, match="not a draw contract document"):
        cb.parse_result(json.dumps({"ok": False, "error": "quota_exhausted"}), 4)


def test_cli_rejects_other_backends_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["draw", "p", "--backend", "chatgpt-browser", "--device", "cuda",
                                      "-o", str(tmp_path / "o.png")])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 2
