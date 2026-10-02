"""Backend names identify transports; tests never access credentials or a network."""
import sys
import pytest
from draw_cli import cli, codex, chatgpt_web

@pytest.mark.parametrize("source", ["argument", "environment"])
@pytest.mark.parametrize("name", ["chatgpt", "codex"])
def test_legacy_alias_warns_and_still_only_uses_codex(monkeypatch, tmp_path, capsys, source, name):
    monkeypatch.setattr(cli, "ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("DRAW_BACKEND", raising=False)
    calls = []
    def inspect(binary):
        calls.append(binary)
        return codex.Installation("fake-codex", "test-client", {})
    def no_browser(**kwargs):
        pytest.fail("An alias must not switch transport to a browser")
    monkeypatch.setattr(codex, "inspect_codex", inspect)
    monkeypatch.setattr(chatgpt_web, "run", no_browser)
    argv = ["draw", "--check"]
    if source == "argument":
        argv += ["--backend", name]
    else:
        monkeypatch.setenv("DRAW_BACKEND", name)
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 0
    assert len(calls) == 1
    result = capsys.readouterr()
    if name == "chatgpt":
        assert "deprecated alias" in result.err
        assert "Codex allowance" in result.err
        assert "--backend codex" in result.err
    else:
        assert "deprecated alias" not in result.err


def test_help_does_not_advertise_alias_as_separate_transport(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["draw", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "chatgpt: deprecated alias of codex" in text
