"""Offline contracts; never access a real browser session or spend usage."""
from __future__ import annotations
import io
import sys
from pathlib import Path
import pytest
from PIL import Image
from draw_cli import cli

@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ENV_FILE", str(tmp_path / "missing.env"))
    for key in ("DRAW_BACKEND", "DRAW_BROWSER_PROFILE", "DRAW_BROWSER_CHANNEL"):
        monkeypatch.delenv(key, raising=False)

@pytest.mark.parametrize("argv,mode", [
    (["login"], "login"),
    (["--backend", "chatgpt-web", "--check"], "check"),
    (["--backend", "chatgpt-web", "airplane made by apple", "-o", "plane.jpg"], "generate"),
])
def test_public_cli_routes_browser_without_codex(monkeypatch, argv, mode):
    from draw_cli import chatgpt_web
    calls = []
    monkeypatch.setattr(chatgpt_web, "run", lambda **kw: calls.append(kw) or 0)
    monkeypatch.setattr(sys, "argv", ["draw"] + argv)
    assert cli.main() == 0
    assert calls[0]["mode"] == mode

@pytest.mark.parametrize("extra", [["--model", "gpt-image-2.5"], ["--codex-model", "x"],
    ["--seed", "1"], ["--provider", "x"], ["-i", "ref.png"], ["--check-resources"]])
def test_browser_rejects_unimplemented_flags(monkeypatch, extra):
    monkeypatch.setattr(sys, "argv", ["draw", "--backend", "chatgpt-web", "cat", "-o", "out.png"] + extra)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2

@pytest.mark.parametrize("suffix,fmt", [(".png", "PNG"), (".jpg", "JPEG"), (".jpeg", "JPEG"), (".webp", "WEBP")])
def test_browser_download_uses_real_format(tmp_path, suffix, fmt):
    from draw_cli import chatgpt_web
    source = tmp_path / "download"
    Image.new("RGBA", (256, 256), (10, 20, 30, 0)).save(source, format="PNG")
    target = tmp_path / ("output" + suffix)
    chatgpt_web.save_download(source, target)
    with Image.open(target) as im:
        assert im.format == fmt
        if fmt == "JPEG":
            assert im.getpixel((0, 0)) == (255, 255, 255)

@pytest.mark.parametrize("bad", [b"not an image", b"<html>please log in</html>"])
def test_invalid_download_keeps_existing_output(tmp_path, bad):
    from draw_cli import chatgpt_web
    source, target = tmp_path / "download", tmp_path / "output.jpg"
    source.write_bytes(bad)
    target.write_bytes(b"original")
    with pytest.raises(chatgpt_web.BrowserError):
        chatgpt_web.save_download(source, target)
    assert target.read_bytes() == b"original"

def test_profile_refuses_to_reuse_a_personal_browser_directory(tmp_path):
    from draw_cli import chatgpt_web
    profile = tmp_path / "personal"
    profile.mkdir()
    (profile / "unrelated-file").write_text("leave unchanged")
    with pytest.raises(chatgpt_web.BrowserError, match="dedicated"):
        chatgpt_web.prepare_profile(profile)
    assert (profile / "unrelated-file").read_text() == "leave unchanged"

def test_dedicated_profile_and_lock(tmp_path):
    from draw_cli import chatgpt_web
    p = chatgpt_web.prepare_profile(tmp_path / "draw-profile")
    assert p.stat().st_mode & 0o777 == 0o700
    assert chatgpt_web.prepare_profile(p) == p
    with chatgpt_web.profile_lock(p):
        with pytest.raises(chatgpt_web.BrowserError, match="already"):
            with chatgpt_web.profile_lock(p):
                pass

def test_symlink_profile_refused(tmp_path):
    from draw_cli import chatgpt_web
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(chatgpt_web.BrowserError, match="symlink"):
        chatgpt_web.prepare_profile(link)

def test_nonzero_codex_quota_is_readable_and_ignores_skill_warning():
    import json
    from draw_cli import codex
    message = "You've hit your usage limit. Try again at 11:54 AM."
    events = [{"type": "item.completed", "item": {"type": "error", "message": "Skill descriptions were shortened"}},
              {"type": "error", "message": message},
              {"type": "turn.failed", "error": {"message": message}}]
    text = codex.failure_message(1, "\n".join(json.dumps(e) for e in events), "")
    assert "11:54 AM" in text and "Codex usage limit" in text
    assert "Skill descriptions" not in text and "thread.started" not in text
    assert "No API fallback" in text


def test_browser_env_default_and_stdin(monkeypatch):
    from draw_cli import chatgpt_web
    calls = []
    monkeypatch.setenv("DRAW_BACKEND", "chatgpt-web")
    monkeypatch.setenv("DRAW_CODEX_MODEL", "must-not-leak")
    monkeypatch.setattr(sys, "stdin", io.StringIO("кот из stdin"))
    monkeypatch.setattr(sys, "argv", ["draw", "-o", "cat.jpg"])
    monkeypatch.setattr(chatgpt_web, "run", lambda **kw: calls.append(kw) or 0)
    assert cli.main() == 0
    assert calls[0]["prompt"] == "кот из stdin"
    assert "model" not in calls[0]

@pytest.mark.parametrize("argv", [
    ["--backend", "hf", "--headed"],
    ["--backend", "codex", "--browser-profile", "unused"],
    ["--backend", "chatgpt-web", "--check", "cat"],
    ["--backend", "chatgpt-web", "--timeout", "0", "cat", "-o", "cat.jpg"],
    ["login", "--headless"], ["login", "--backend", "codex"],
])
def test_cli_invalid_browser_combinations(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["draw"] + argv)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2

@pytest.mark.parametrize("kwargs", [
    {"out_path": "bad.gif"}, {"prompt": " "}, {"prompt": "я" * 600000},
    {"timeout": float("nan")}, {"timeout": 0}, {"channel": "arbitrary"},
])
def test_bad_inputs_do_not_open_browser(tmp_path, capsys, kwargs):
    from draw_cli import chatgpt_web
    options = dict(mode="generate", prompt="cat", out_path=str(tmp_path / "out.png"), profile=tmp_path / "not-created")
    options.update(kwargs)
    assert chatgpt_web.run(**options) == 1
    assert not (tmp_path / "not-created").exists()
    assert "dependency missing" not in capsys.readouterr().err

@pytest.mark.parametrize("message", ["You've hit your usage limit", "You have reached the image generation limit", "Too many requests", "Достигнут лимит создания изображений"])
def test_image_quota_classification(message):
    from draw_cli import chatgpt_web
    assert "usage limit" in chatgpt_web.response_failure(message)

def test_linked_download_rejected(tmp_path):
    from draw_cli import chatgpt_web
    import os
    source = tmp_path / "real.png"
    Image.new("RGB", (256, 256)).save(source)
    linked = tmp_path / "linked.png"
    os.link(source, linked)
    with pytest.raises(chatgpt_web.BrowserError, match="regular"):
        chatgpt_web.save_download(linked, tmp_path / "out.png")


def test_codex_nonzero_quota_preserves_output_and_never_retries(monkeypatch, tmp_path):
    from draw_cli import codex
    calls = []
    monkeypatch.setattr(codex, "inspect_codex", lambda _: codex.Installation("fake", "test", {"CODEX_HOME": str(tmp_path)}))
    def failed(*args, **kwargs):
        calls.append(1)
        return 1, '{"type":"turn.failed","error":{"message":"Usage limit. Try again at 11:54 AM."}}', "unrelated warning"
    monkeypatch.setattr(codex, "_run", failed)
    target = tmp_path / "old.jpg"
    target.write_bytes(b"old image")
    with pytest.raises(codex.CodexError, match="11:54 AM"):
        codex.generate("cat", str(target))
    assert calls == [1] and target.read_bytes() == b"old image"
