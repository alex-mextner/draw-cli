"""No app credentials, user browser profiles or live ChatGPT requests in these tests."""
import json
from pathlib import Path
import pytest
from draw_cli import cli

@pytest.mark.parametrize("value", ["https://127.0.0.1:9236", "http://example.com:9236", "http://user:pass@127.0.0.1:9236", "http://127.0.0.1:9236/private", "http://127.0.0.1:9236/?token=x", "http://127.0.0.1:9236/#x", "http://127.0.0.1", "http://0.0.0.0:9236", "http://localhost:9236", "http://127.0.0.1:9236/\n"])
def test_remote_credential_bearing_or_ambiguous_endpoints_refused(value):
    from draw_cli import app_session
    with pytest.raises(ValueError):
        app_session.checked_endpoint(value)

@pytest.mark.parametrize("value", ["http://127.0.0.1:9236", "http://[::1]:9236/"])
def test_numeric_loopback_endpoint(value):
    from draw_cli import app_session
    assert app_session.checked_endpoint(value) == value.rstrip("/")

@pytest.mark.parametrize("ws", ["ws://remote.example/devtools/browser/id", "ws://127.0.0.1:9999/devtools/browser/id", "ws://user:pass@127.0.0.1:9236/devtools/browser/id", "ws://127.0.0.1:9236/devtools/browser/id?secret=x", "ws://127.0.0.1:9236/not-browser/id"])
def test_discovery_must_not_redirect_to_other_server(ws):
    from draw_cli import app_session
    with pytest.raises(ValueError):
        app_session.checked_websocket("http://127.0.0.1:9236", ws)

def test_report_has_private_permissions(tmp_path):
    from draw_cli import app_session
    p=app_session.write_report({"status":"not_connected"}, parent=tmp_path)
    assert p.parent.stat().st_mode & 0o777 == 0o700
    assert p.stat().st_mode & 0o777 == 0o600
    assert json.loads(p.read_text()) == {"status":"not_connected"}

def test_running_app_is_never_restarted(monkeypatch):
    from draw_cli import app_session
    monkeypatch.setattr(app_session,"app_pids",lambda: [123])
    monkeypatch.setattr(app_session.subprocess,"run",lambda *a,**kw: pytest.fail("must not launch, signal, or quit"))
    with pytest.raises(RuntimeError,match="already running"):
        app_session.launch_app(9236)

def test_cli_app_session_routes(monkeypatch):
    import sys
    from draw_cli import app_session
    calls=[]
    monkeypatch.setattr(app_session,"main",lambda args:calls.append(args) or 0)
    monkeypatch.setattr(sys,"argv",["draw","app-session","--probe-web-session"])
    assert cli.main()==0 and calls==[["--probe-web-session"]]

def test_cli_cdp_routes_without_profile_flags(monkeypatch,tmp_path):
    import sys
    from draw_cli import chatgpt_web
    monkeypatch.setattr(cli,"ENV_FILE",str(tmp_path/'none'))
    monkeypatch.delenv("DRAW_BACKEND",raising=False)
    calls=[]
    monkeypatch.setattr(chatgpt_web,"run",lambda **kw:calls.append(kw) or 0)
    monkeypatch.setattr(sys,"argv",["draw","--backend","chatgpt-web","--cdp-url","http://127.0.0.1:9236","--check"])
    assert cli.main()==0
    assert calls[0]["cdp_url"]=="http://127.0.0.1:9236"

@pytest.mark.parametrize("extra", [["--browser-profile","private"],["--browser-channel","chrome"],["--headed"]])
def test_cdp_rejects_launch_controls(monkeypatch,tmp_path,extra):
    import sys
    monkeypatch.setattr(cli,"ENV_FILE",str(tmp_path/'none'))
    monkeypatch.setattr(sys,"argv",["draw","--backend","chatgpt-web","--cdp-url","http://127.0.0.1:9236","--check"]+extra)
    with pytest.raises(SystemExit) as e:cli.main()
    assert e.value.code==2
