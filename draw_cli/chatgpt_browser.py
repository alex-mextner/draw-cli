"""chatgpt-browser backend: ChatGPT in a dedicated, logged-in browser profile.

draw does not drive the browser itself. It speaks the DrawBackend JSON contract (v1) to a companion
command that owns the browser: one JSON request on stdin, exactly one JSON result on stdout, exit
code = failure class. The reference implementation is `hsw-editor draw run --backend chatgpt-browser`
(https://github.com/alex-mextner/hsw-editor, docs/draw.md); any command speaking the contract works.

Rules enforced by the companion, not weakened here: dedicated profile (never an existing browser),
single owner lock, human pacing, no stealth, no captcha solving, no cookie export, quota/limit state
persisted. draw never retries a generation automatically.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path

from draw_cli.backends import DrawError, save_image, safe_error
from draw_cli.codex import _stop

DEFAULT_COMMAND = "hsw-editor draw"
MAX_REFERENCES = 5
# Failure class -> process exit code (contract v1).
EXIT_CODES = {"invalid_request": 2, "login_required": 3, "quota_exhausted": 4, "challenge": 5,
              "timeout": 6, "backend_unavailable": 7, "refused": 8, "manual_pending": 10}
LOGIN_HINT = ("one-time setup: run `hsw-editor draw login chatgpt`, log in to ChatGPT in the window "
              "that opens, then close it")


class BrowserError(DrawError):
    """The browser backend failed; `code` is the contract failure class."""

    def __init__(self, message: str, code: str = "backend_unavailable"):
        super().__init__(message)
        self.code = code


def command_argv(command: str | None = None) -> list[str]:
    try:
        argv = shlex.split(command or os.environ.get("DRAW_CHATGPT_BROWSER_CMD") or DEFAULT_COMMAND)
    except ValueError as exc:
        raise BrowserError(f"DRAW_CHATGPT_BROWSER_CMD is not valid shell quoting: {exc}") from None
    if not argv:
        raise BrowserError("DRAW_CHATGPT_BROWSER_CMD is empty")
    return argv


def build_request(prompt: str, references: list[str], out_dir: str, *, timeout: float = 600,
                  tag: str = "draw-cli") -> dict:
    if not prompt.strip():
        raise BrowserError("prompt is required", "invalid_request")
    if len(prompt) > 8000:
        raise BrowserError("prompt exceeds 8000 characters", "invalid_request")
    if len(references) > MAX_REFERENCES:
        raise BrowserError(f"at most {MAX_REFERENCES} reference images", "invalid_request")
    refs = []
    for ref in references:
        path = Path(ref).expanduser().absolute()
        if not path.is_file():
            raise BrowserError(f"reference image not found: {ref}", "invalid_request")
        refs.append({"path": str(path), **({"role": "subject"} if not refs else {})})
    return {"version": 1, "prompt": prompt, "refs": refs, "n": 1, "size": "1024x1024", "outDir": out_dir,
            "timeoutMs": max(5000, min(1_800_000, int(timeout * 1000))), "tag": tag}


def _run(argv: list[str], payload: str | None, timeout: float) -> subprocess.CompletedProcess:
    # Own process group: on timeout or Ctrl-C the whole companion tree (it owns a browser and a lock)
    # is stopped, so an orphan cannot keep the single-owner lock and fail the next run.
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=os.name == "posix")
    except FileNotFoundError:
        raise BrowserError(
            f"companion command not found: {argv[0]}. Install hsw-editor (alex-mextner/hsw-editor) or set "
            "DRAW_CHATGPT_BROWSER_CMD to a command that speaks the draw contract") from None
    try:
        stdout, stderr = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop(proc)
        raise BrowserError("browser backend timed out; the process was stopped", "timeout") from None
    except BaseException:
        _stop(proc)
        raise
    finally:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def parse_result(stdout: str, returncode: int) -> dict:
    """Parse the single JSON document and check it agrees with the exit code."""
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    result = None
    for candidate in (stdout.strip(), lines[-1] if lines else ""):  # whole document, else last line
        try:
            result = json.loads(candidate)
            break
        except ValueError:
            continue
    if result is None:
        raise BrowserError(f"companion printed no JSON result (exit {returncode})")
    if (not isinstance(result, dict) or not isinstance(result.get("ok"), bool)
            or (not result["ok"] and not isinstance(result.get("error"), dict))):
        raise BrowserError("companion result is not a draw contract document")
    expected = 0 if result["ok"] else EXIT_CODES.get((result.get("error") or {}).get("code"), -1)
    if returncode != expected:
        raise BrowserError(f"companion exit {returncode} disagrees with its result (expected {expected})")
    return result


def failure_message(result: dict) -> str:
    error = result.get("error") or {}
    code, message = error.get("code", "backend_unavailable"), safe_error(error.get("message", ""))
    if code == "login_required":
        return f"ChatGPT browser is not logged in ({message}); {LOGIN_HINT}"
    if code == "quota_exhausted":
        retry = error.get("retryAfterMs")
        after = f"; retry in about {max(1, round(retry / 60000))} min" if isinstance(retry, (int, float)) else ""
        return f"ChatGPT image limit reached ({message}){after}"
    if code == "challenge":
        return ("ChatGPT asked for human verification; solve it yourself in the dedicated browser "
                f"(`hsw-editor draw login chatgpt`). Not retried. ({message})")
    return f"{code}: {message}"


def generate(prompt: str, out_path: str, *, references: list[str] | None = None, timeout: float = 600,
             command: str | None = None) -> None:
    from PIL import Image

    argv = command_argv(command)
    with tempfile.TemporaryDirectory(prefix="draw-browser-") as work:
        request = build_request(prompt, references or [], work, timeout=timeout)
        proc = _run(argv + ["run", "--backend", "chatgpt-browser"], json.dumps(request), timeout + 60)
        result = parse_result(proc.stdout, proc.returncode)
        if not result["ok"]:
            code = (result.get("error") or {}).get("code", "backend_unavailable")
            raise BrowserError(failure_message(result), code)
        images = result.get("images")
        first = images[0] if isinstance(images, list) and images else None
        if not isinstance(first, dict) or not isinstance(first.get("path"), str):
            raise BrowserError("companion reported success without a usable image", "refused")
        source = Path(first["path"]).resolve()
        if Path(work).resolve() not in source.parents:
            raise BrowserError("companion returned an image outside the work directory", "refused")
        with Image.open(source) as image:
            image.load()
            save_image(image, out_path)


def inspect(command: str | None = None) -> str:
    """Report the chatgpt-browser state without generating anything."""
    proc = _run(command_argv(command) + ["status", "--json"], None, 90)
    try:
        report = json.loads(proc.stdout)
        entry = next(b for b in report["backends"] if b["id"] == "chatgpt-browser")
        state = entry["state"]
    except (ValueError, KeyError, StopIteration, TypeError):
        raise BrowserError("companion `status --json` did not report chatgpt-browser") from None
    if state != "ready":
        steps = "; ".join(entry.get("steps") or [])
        code = state if state in EXIT_CODES else "backend_unavailable"
        raise BrowserError(f"chatgpt-browser is {state}: {entry.get('message', '')}"
                           + (f". Steps: {steps}" if steps else ""), code)
    return f"chatgpt-browser ready ({entry.get('message', '')}); no generation performed"
