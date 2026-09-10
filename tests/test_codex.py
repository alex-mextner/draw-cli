"""Offline contract tests: a real fake CLI process, never a real login/request."""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from draw_cli import cli, codex  # noqa: E402

THREAD = "01993034-3060-7000-8000-0123456789ab"

FAKE = r'''
import base64, json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
mode = os.environ.get("FAKE_MODE", "ok")
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"argv": args}) + "\n")
if args == ["--version"]:
    print("codex-cli test-fixture")
    raise SystemExit(1 if mode == "version_error" else 0)
if args == ["login", "status"]:
    print("Logged in using an API key - sk-fake" if mode == "api" else
          "Not logged in" if mode == "noauth" else "Logged in using ChatGPT", file=sys.stderr)
    raise SystemExit(1 if mode == "noauth" else 0)
if args == ["exec", "--help"]:
    print("--json --ephemeral" if mode == "old" else
          "--ignore-user-config --ignore-rules --ephemeral --json --skip-git-repo-check --sandbox --cd --image --color")
    raise SystemExit(0)
if args == ["features", "list"]:
    print("shell_tool stable true" if mode == "nofeature" else "image_generation stable false")
    raise SystemExit(0)
assert "exec" in args and args[-1] == "-", args
workspace = Path(args[args.index("--cd") + 1])
refs = [args[i+1] for i, value in enumerate(args) if value == "--image"]
record = {"prompt": sys.stdin.read(), "cwd": str(Path.cwd()), "workspace": str(workspace),
          "ref_bytes": [base64.b64encode(Path(p).read_bytes()).decode() for p in refs],
          "env_keys": list(os.environ), "home": os.environ["CODEX_HOME"],
          "guard": os.environ.get("DRAW_CODEX_ACTIVE")}
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(record) + "\n")
if mode == "timeout":
    time.sleep(30)
if mode == "nonzero":
    print("failed: Bearer private-token sk-secret", file=sys.stderr)
    raise SystemExit(7)
thread = "01993034-3060-7000-8000-0123456789ab"
print(json.dumps({"type": "thread.started", "thread_id": thread}))
if mode == "failed":
    print(json.dumps({"type": "turn.failed", "error": {"message": "image quota exceeded"}}))
    raise SystemExit(0)
if mode == "textonly":
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "saved /tmp/not-real.png"}}))
else:
    root = (workspace / "generated_images" if mode == "workspace" else
            Path(os.environ["CODEX_HOME"]) / "generated_images" / thread)
    root.mkdir(parents=True)
    data = base64.b64decode(os.environ["FAKE_PNG"])
    (root / "call_1.png").write_bytes(b"not an image" if mode == "corrupt" else data)
    if mode == "multiple":
        (root / "call_2.png").write_bytes(data)
    if mode == "oldartifact":
        os.utime(root / "call_1.png", (1, 1))
    if mode == "symlink":
        path = root / "call_1.png"
        original = workspace / "original.png"
        path.rename(original)
        path.symlink_to(original)
if mode != "incomplete":
    print(json.dumps({"type": "turn.completed", "usage": {}}))
'''


@pytest.fixture
def png() -> bytes:
    buffer = io.BytesIO()
    meta = PngImagePlugin.PngInfo()
    meta.add_text("test-metadata", "preserve this")
    Image.new("RGBA", (4, 3), (40, 80, 120, 100)).save(buffer, "PNG", pnginfo=meta)
    return buffer.getvalue()


@pytest.fixture
def fake(tmp_path, monkeypatch, png):
    for key in list(os.environ):
        if key.startswith("DRAW_"):
            monkeypatch.delenv(key)
    binary = tmp_path / "codex with spaces"
    binary.write_text(f"#!{sys.executable}\n" + FAKE)
    binary.chmod(0o755)
    home = tmp_path / "custom codex home"
    home.mkdir()
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_PNG", base64.b64encode(png).decode())
    monkeypatch.setenv("FAKE_MODE", "ok")
    monkeypatch.setattr(cli, "ENV_FILE", str(tmp_path / "missing.env"))
    return binary, home, log


def records(fake):
    return [json.loads(line) for line in fake[2].read_text().splitlines()]


def test_subscription_pipeline_and_safety_flags(fake, tmp_path, monkeypatch, png):
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY", "HF_TOKEN"):
        monkeypatch.setenv(key, "secret-not-forwarded")
    output = tmp_path / "image ; not a command.png"
    prompt = 'Нарисуй кота\n$(touch never-created) `echo nope` "quotes"'
    codex.generate(prompt, str(output), binary=str(fake[0]))
    assert output.read_bytes() == png
    calls = records(fake)
    assert len(calls) == 6
    argv, run = calls[-2]["argv"], calls[-1]
    assert run["prompt"] == prompt and prompt not in argv
    assert run["cwd"] == run["workspace"] != str(tmp_path)
    assert not Path(run["workspace"]).exists()
    assert run["home"] == str(fake[1]) and run["guard"] == "1"
    assert not set(run["env_keys"]) & {"OPENAI_API_KEY", "OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY", "HF_TOKEN"}
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv
    assert "--ephemeral" in argv and "--json" in argv
    assert argv[argv.index("--sandbox")+1] == "read-only"
    assert argv[argv.index("--ask-for-approval")+1] == "never"
    assert 'forced_login_method="chatgpt"' in argv
    assert 'model_provider="openai"' in argv
    assert 'features.shell_tool=false' in argv and 'features.image_generation=true' in argv
    assert "--model" not in argv and not any("gpt-image" in a for a in argv)
    assert "--yolo" not in argv and "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert (fake[1] / "generated_images" / THREAD / "call_1.png").read_bytes() == png
    assert os.environ["OPENAI_API_KEY"] == "secret-not-forwarded"


@pytest.mark.parametrize("suffix,fmt", [("png", "PNG"), ("jpg", "JPEG"), ("jpeg", "JPEG"), ("webp", "WEBP")])
def test_output_format_is_real_not_just_renamed(fake, tmp_path, suffix, fmt):
    output = tmp_path / f"out.{suffix}"
    codex.generate("cat", str(output), binary=str(fake[0]))
    with Image.open(output) as image:
        assert image.format == fmt and image.size == (4, 3)
        if fmt == "JPEG":
            assert image.mode == "RGB"


def test_workspace_native_artifact(fake, tmp_path, monkeypatch, png):
    monkeypatch.setenv("FAKE_MODE", "workspace")
    output = tmp_path / "out.png"
    codex.generate("cat", str(output), binary=str(fake[0]))
    assert output.read_bytes() == png


def test_old_mtime_in_current_thread_is_still_valid(fake, tmp_path, monkeypatch, png):
    monkeypatch.setenv("FAKE_MODE", "oldartifact")
    output = tmp_path / "out.png"
    codex.generate("cat", str(output), binary=str(fake[0]))
    assert output.read_bytes() == png


def test_references_staged_and_reasoning_model_forwarded(fake, tmp_path, png):
    reference = tmp_path / "reference, with spaces.png"
    reference.write_bytes(png)
    codex.generate("edit", str(tmp_path / "out.png"), binary=str(fake[0]),
                   references=[str(reference)], model="agent-model-from-user")
    argv, run = records(fake)[-2]["argv"], records(fake)[-1]
    assert argv[argv.index("--model") + 1] == "agent-model-from-user"
    assert str(reference) not in argv
    assert len(run["ref_bytes"]) == 1
    with Image.open(io.BytesIO(base64.b64decode(run["ref_bytes"][0]))) as staged:
        assert staged.size == (4, 3) and staged.format == "PNG"
    assert reference.read_bytes() == png


@pytest.mark.parametrize("mode,match", [
    ("api", "ChatGPT login required"), ("noauth", "ChatGPT login required"),
    ("old", "isolated exec options"), ("nofeature", "does not advertise"),
    ("version_error", "cannot read Codex version"),
])
def test_preflight_refuses_without_generating(fake, monkeypatch, mode, match):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(codex.CodexError, match=match):
        codex.inspect_codex(str(fake[0]))
    assert not any("prompt" in r for r in records(fake))


def test_missing_binary_and_recursion(fake, monkeypatch):
    with pytest.raises(codex.CodexError, match="not found"):
        codex.inspect_codex("/nonexistent/codex")
    monkeypatch.setenv("DRAW_CODEX_ACTIVE", "1")
    with pytest.raises(codex.CodexError, match="recursive"):
        codex.inspect_codex(str(fake[0]))
    assert not fake[2].exists()


@pytest.mark.parametrize("mode,match", [
    ("nonzero", "status 7"), ("failed", "image quota exceeded"),
    ("textonly", "no native image artifact"), ("corrupt", "invalid image"),
    ("multiple", "multiple image artifacts"),
    ("symlink", "linked Codex image"), ("incomplete", "completed new thread"),
])
def test_failures_preserve_output_and_do_not_retry(fake, tmp_path, monkeypatch, mode, match):
    monkeypatch.setenv("FAKE_MODE", mode)
    output = tmp_path / "out.png"
    output.write_bytes(b"original untouched")
    with pytest.raises(codex.CodexError, match=match) as error:
        codex.generate("cat", str(output), binary=str(fake[0]))
    assert "sk-secret" not in str(error.value) and "private-token" not in str(error.value)
    assert output.read_bytes() == b"original untouched"
    assert len([r for r in records(fake) if "prompt" in r]) == 1
    assert not list(tmp_path.glob(".draw-*"))


def test_timeout_stops_and_does_not_retry(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "timeout")
    start = time.monotonic()
    with pytest.raises(codex.CodexError, match="timed out"):
        codex.generate("cat", str(tmp_path / "out.png"), binary=str(fake[0]), timeout=2)
    assert time.monotonic() - start < 15
    assert not (tmp_path / "out.png").exists()
    assert len([r for r in records(fake) if "prompt" in r]) == 1


@pytest.mark.parametrize("kwargs,match", [
    ({"prompt": " "}, "cannot be empty"), ({"timeout": 0}, "positive finite"),
    ({"timeout": float("inf")}, "positive finite"), ({"timeout": float("nan")}, "positive finite"),
    ({"model": "gpt-image-2.5"}, "not the image model"),
    ({"references": ["r.png"] * 6}, "at most five"),
    ({"out_path": "bad.gif"}, "output path"),
])
def test_bad_inputs_fail_before_client(fake, tmp_path, kwargs, match):
    options = dict(prompt="cat", out_path=str(tmp_path / "out.png"), binary=str(fake[0]))
    options.update(kwargs)
    with pytest.raises(codex.CodexError, match=match):
        codex.generate(**options)
    assert not fake[2].exists()


def test_invalid_reference_has_no_codex_process_at_all(fake, tmp_path):
    reference = tmp_path / "bad.png"
    reference.write_text("not an image")
    with pytest.raises(codex.CodexError, match="invalid image"):
        codex.generate("edit", str(tmp_path / "out.png"), binary=str(fake[0]), references=[str(reference)])
    assert not fake[2].exists()


def test_symlink_output_and_missing_parent_fail_before_generation(fake, tmp_path):
    output = tmp_path / "out.png"
    output.symlink_to(tmp_path / "other.png")
    with pytest.raises(codex.CodexError, match="symlink"):
        codex.generate("cat", str(output), binary=str(fake[0]))
    with pytest.raises(codex.CodexError, match="directory does not exist"):
        codex.generate("cat", str(tmp_path / "missing" / "out.png"), binary=str(fake[0]))
    assert not fake[2].exists()


def test_check_cli_never_reads_prompt_or_generates(fake, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["draw", "--backend", "chatgpt", "--check", "--codex-bin", str(fake[0])])
    assert cli.main() == 0
    text = capsys.readouterr().out
    assert "ChatGPT login" in text and "not verified" in text
    assert len(records(fake)) == 4


@pytest.mark.parametrize("provider", ["chatgpt", "codex"])
def test_cli_subscription_stdin_and_env_default(fake, tmp_path, monkeypatch, provider, png):
    monkeypatch.setenv("DRAW_BACKEND", provider)
    monkeypatch.setenv("DRAW_CODEX_BIN", str(fake[0]))
    monkeypatch.setenv("HF_MODEL", "must-not-leak-to-codex")
    monkeypatch.setattr(sys, "stdin", io.StringIO("кот из stdin\n"))
    output = tmp_path / "out.png"
    monkeypatch.setattr(sys, "argv", ["draw", "-o", str(output)])
    assert cli.main() == 0 and output.read_bytes() == png
    assert records(fake)[-1]["prompt"] == "кот из stdin"


def test_hf_default_still_uses_hf_model(fake, monkeypatch):
    captured = []
    monkeypatch.setenv("HF_MODEL", "existing/hf-model")
    monkeypatch.setattr(cli, "generate", lambda *args, **kwargs: captured.append((args, kwargs)))
    monkeypatch.setattr(sys, "argv", ["draw", "cat", "-o", "out.png"])
    assert cli.main() == 0
    args, kwargs = captured[0]
    assert args == ("cat", "existing/hf-model", "out.png")
    assert kwargs["backend"] == "hf"
    assert not fake[2].exists()


@pytest.mark.parametrize("args", [
    ["--backend", "chatgpt", "--model", "gpt-image-2.5", "cat", "-o", "out.png"],
    ["--backend", "chatgpt", "--check", "cat"],
    ["--backend", "hf", "--check"],
    ["--backend", "hf", "-i", "ref.png", "cat", "-o", "out.png"],
    ["--backend", "chatgpt", "cat"],
    ["--backend", "chatgpt", "--timeout", "0", "cat", "-o", "out.png"],
])
def test_cli_rejects_ambiguous_or_invalid_options(fake, monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["draw"] + args)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert not fake[2].exists()


def test_cli_environment_provider_validation(fake, monkeypatch):
    monkeypatch.setenv("DRAW_BACKEND", "typo")
    monkeypatch.setattr(sys, "argv", ["draw", "cat", "-o", "out.png"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("stream", [
    "not json\n[]\n", json.dumps({"type": "thread.started", "thread_id": "../../private"}),
    json.dumps({"type": "thread.started", "thread_id": THREAD}) + "\n" + json.dumps({"type": "thread.started", "thread_id": "01993034-3060-7000-8000-0123456789ac"}),
])
def test_untrusted_thread_ids_and_missing_completion(stream):
    with pytest.raises(codex.CodexError):
        codex._thread_result(stream)


def test_artifact_lookup_ignores_other_threads(fake, tmp_path, png):
    old = fake[1] / "generated_images" / "another-thread"
    old.mkdir(parents=True)
    (old / "image.png").write_bytes(png)
    with pytest.raises(codex.CodexError, match="no native image"):
        codex._read_artifact(fake[1], tmp_path, THREAD, time.time())


def test_hardlinked_artifact_rejected(fake, tmp_path, png):
    root = fake[1] / "generated_images" / THREAD
    root.mkdir(parents=True)
    original = tmp_path / "original.png"
    original.write_bytes(png)
    os.link(original, root / "call.png")
    with pytest.raises(codex.CodexError, match="linked Codex image"):
        codex._read_artifact(fake[1], tmp_path, THREAD, time.time())


def test_artifact_directory_symlink_rejected(fake, tmp_path, png):
    root = fake[1] / "generated_images"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "call.png").write_bytes(png)
    (root / THREAD).symlink_to(outside, target_is_directory=True)
    with pytest.raises(codex.CodexError, match="symlinked Codex artifact"):
        codex._read_artifact(fake[1], tmp_path, THREAD, time.time())


def test_oversized_output_is_bounded(fake, monkeypatch):
    monkeypatch.setattr(codex, "MAX_EVENT_BYTES", 8)
    with pytest.raises(codex.CodexError, match="event limit"):
        codex._run([sys.executable, "-c", "print('x'*20)"], os.environ.copy())


def test_save_failure_does_not_replace_existing(tmp_path, png, monkeypatch):
    output = tmp_path / "out.png"
    output.write_bytes(b"existing")

    def fail(*args):
        raise OSError("test replace failure")

    monkeypatch.setattr(codex.os, "replace", fail)
    with pytest.raises(OSError, match="replace failure"):
        codex._save(png, output)
    assert output.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".draw-*"))


def test_diagnostics_redact_credentials_and_control_codes():
    result = codex._diagnostic("\x1b[31merror\x00 sk-private hf_private Bearer private eyJabcdefgh.abc.xyz")
    assert "private" not in result and "eyJ" not in result and "\x1b" not in result and "\x00" not in result
