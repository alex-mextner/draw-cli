"""Opt-in app session diagnostics and attachment, never credential extraction.

Only public app metadata, process identity, loopback CDP discovery and UI readiness
are inspected. No auth files, Keychain, cookie/storage exports, private API calls,
application patching, permission changes, stealth, or challenge solving.
"""
from __future__ import annotations
import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

APP = Path("/Applications/ChatGPT.app")
DEFAULT_PORT = 9236


def checked_endpoint(value: str) -> str:
    if not isinstance(value, str) or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise ValueError("CDP requires an explicit numeric loopback HTTP URL with a port.")
    u = urlsplit(value)
    if (u.scheme != "http" or u.hostname not in ("127.0.0.1", "::1")
            or not u.port or u.username is not None or u.password is not None
            or u.query or u.fragment or u.path not in ("", "/") or "?" in value or "#" in value):
        raise ValueError("CDP requires an explicit numeric loopback HTTP URL with a port, without credentials or extra paths.")
    return value.rstrip("/")


def checked_websocket(endpoint: str, value: str) -> str:
    base = urlsplit(checked_endpoint(endpoint))
    if not isinstance(value, str) or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise ValueError("Invalid CDP discovery response.")
    u = urlsplit(value)
    if (u.scheme != "ws" or u.hostname != base.hostname or u.port != base.port
            or u.username is not None or u.password is not None or u.query or u.fragment
            or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9_-]+", u.path)
            or "?" in value or "#" in value):
        raise ValueError("CDP discovery must refer to the same loopback browser, without credentials.")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("CDP discovery redirects are not accepted.")


def discover(endpoint: str) -> str:
    endpoint = checked_endpoint(endpoint)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(endpoint + "/json/version", timeout=3) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("CDP discovery response exceeds limit.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid CDP discovery response.")
    return checked_websocket(endpoint, data.get("webSocketDebuggerUrl"))


class ContextSelectionError(RuntimeError):
    def __init__(self, contexts):
        super().__init__("Multiple browser contexts; inspect the report and select --cdp-context explicitly.")
        self.count = len(contexts)
        self.chatgpt_tabs = [sum(urlsplit(page.url).netloc == "chatgpt.com" for page in c.pages) for c in contexts]


@contextmanager
def attached_context(playwright, endpoint: str, *, artifacts: Path, context_index=None):
    """Connect without changing global download/emulation defaults. Never close the app."""
    websocket = discover(endpoint)
    browser = playwright.chromium.connect_over_cdp(
        websocket, timeout=15000, no_defaults=True, is_local=True,
        artifacts_dir=str(artifacts),
    )
    try:
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("The connected application exposes no browser context.")
        if context_index is None:
            if len(contexts) != 1:
                raise ContextSelectionError(contexts)
            context_index = 0
        if isinstance(context_index, bool) or not isinstance(context_index, int) or not 0 <= context_index < len(contexts):
            raise RuntimeError("The requested browser context does not exist.")
        yield contexts[context_index]
    finally:
        # Connected browser.close() disconnects; no Browser.close CDP command or
        # context.close() is sent. Only caller-created pages may be closed.
        browser.close()


def app_pids() -> list[int]:
    result = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True, timeout=5, check=True)
    found = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) == 3 and fields[2] == str(APP / "Contents/MacOS/ChatGPT"):
            found.append(int(fields[0]))
    return found


def app_listeners(pids: list[int], port: int) -> list[str]:
    bindings = []
    for pid in pids:
        result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"], capture_output=True, text=True, timeout=5)
        if result.returncode not in (0, 1):
            raise RuntimeError("Cannot verify the app listener owner.")
        for line in result.stdout.splitlines():
            if line.startswith("n") and line[1:].endswith(":" + str(port)):
                bindings.append(line[1:])
    return bindings


def launch_app(port: int) -> None:
    if app_pids():
        raise RuntimeError("ChatGPT is already running. Save your work and quit it yourself before --launch; draw never restarts it.")
    if not APP.is_dir() or not 1024 <= port <= 65535:
        raise RuntimeError("ChatGPT app is missing or port is invalid.")
    # A normal application launch with a documented Chromium debugging switch.
    # No alternative user-data directory, credential copies or modified binaries.
    subprocess.run(["/usr/bin/open", "-a", str(APP), "--args",
                    "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}"],
                   check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_report(report: dict, *, parent: Path = Path("/tmp")) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="draw-app-report-", dir=parent))
    directory.chmod(0o700)
    path = directory / "report.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path


def probe_session(endpoint: str, context_index=None) -> dict:
    from playwright.sync_api import sync_playwright
    from draw_cli.chatgpt_web import HOME, readiness
    with tempfile.TemporaryDirectory(prefix="draw-app-probe-") as scratch, sync_playwright() as p:
        with attached_context(p, endpoint, artifacts=Path(scratch), context_index=context_index) as context:
            # Only our new tab is visited; existing tabs and app UI stay untouched.
            page = context.new_page()
            try:
                page.goto(HOME, wait_until="domcontentloaded", timeout=15000)
                deadline = time.monotonic() + 10
                state = "loading"
                while time.monotonic() < deadline:
                    state = readiness(page)
                    if state != "loading":
                        break
                    page.wait_for_timeout(250)
                return {"web_session_status": state, "new_tab_auth_verified": state == "ready",
                        "image_generation_verified": False}
            finally:
                page.close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="draw app-session", allow_abbrev=False,
        description="Inspect app automation without credentials; --launch opens a local control port only when the app is closed.")
    parser.add_argument("--launch", action="store_true", help="explicitly launch the closed app with a loopback debugging port; trusted local processes can control it")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--probe-web-session", action="store_true", help="open and close one check tab; no prompt, image or auth export")
    parser.add_argument("--cdp-context", type=int)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    report = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
              "credentials_exported": False, "image_generation_verified": False,
              "app_present": APP.is_dir(), "endpoint": f"http://127.0.0.1:{args.port}",
              "status": "not_connected"}
    code = 3
    try:
        with (APP / "Contents/Info.plist").open("rb") as f:
            info = plistlib.load(f)
        version = info.get("CFBundleShortVersionString", "")
        report["app_version"] = version if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9.-]{1,80}", version) else "unknown"
        report["applescript_declared"] = info.get("NSAppleScriptEnabled") is True
        if args.launch:
            launch_app(args.port)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if app_listeners(app_pids(), args.port):
                    break
                time.sleep(0.25)
        pids = app_pids()
        report["app_running"] = bool(pids)
        bindings = app_listeners(pids, args.port)
        allowed = {f"127.0.0.1:{args.port}", f"[::1]:{args.port}"}
        if not bindings:
            report["status"] = "app_control_unavailable"
            report["next_action"] = "Save your work, quit ChatGPT yourself, then run draw app-session --launch --probe-web-session. No automatic restart is performed."
        elif any(binding not in allowed for binding in bindings):
            report["status"] = "unsafe_listener_binding"
            report["next_action"] = "Quit the app to close its debugging port. draw did not attach to a non-loopback listener."
        else:
            discover(report["endpoint"])
            report["status"] = "app_control_ready"
            code = 0
            if args.probe_web_session:
                report.update(probe_session(report["endpoint"], args.cdp_context))
                code = 0 if report["new_tab_auth_verified"] else 3
    except Exception as exc:
        # Fixed categories only: never persist arbitrary service/driver responses.
        report["status"] = "probe_failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, ContextSelectionError):
            report["status"] = "context_selection_required"
            report["context_count"] = exc.count
            report["existing_chatgpt_tab_counts"] = exc.chatgpt_tabs
            report["next_action"] = "Select --cdp-context using a reported context index, then probe again. No URLs, titles or credentials were exported."
        if isinstance(exc, RuntimeError) and "already running" in str(exc):
            report["status"] = "app_already_running"
            report["next_action"] = "Save your work and quit ChatGPT yourself before --launch; your running app was not changed."
    path = write_report(report)
    print(json.dumps({"status": report["status"], "report": str(path),
                      "web_session_status": report.get("web_session_status", "not_checked")}, ensure_ascii=False))
    return code
