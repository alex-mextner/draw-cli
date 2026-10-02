"""Experimental ChatGPT UI adapter using a dedicated, user-signed-in browser.

No Desktop/Keychain inspection, credential export, private HTTP API, Codex calls,
stealth flags, automatic retries, or paid fallback. Browser owns its session.
"""
from __future__ import annotations

import math
import os
import re
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from draw_cli.codex import (CodexError, FORMATS, MAX_IMAGE_BYTES,
                            MAX_PROMPT_BYTES, _diagnostic, _image, _save)

HOME = "https://chatgpt.com/"
MARKER = ".draw-cli-profile"
MARKER_CONTENT = "draw-cli dedicated browser profile v1\n"
COMPOSER = '#prompt-textarea[contenteditable="true"], textarea#prompt-textarea'
ACCOUNT = ('[data-testid="accounts-profile-button"], [data-testid="profile-button"], '
           'button[aria-label="Open profile menu"], button[aria-label="Открыть меню профиля"]')
GENERATED = ('img[alt^="Generated image"], img[alt^="Image created"], '
             'img[alt^="Сгенерированное изображение"], img[alt^="Созданное изображение"]')
DOWNLOAD = re.compile(r"^(?:(?:download|save)(?: (?:this|the))?(?: image)?|скачать(?: изображение)?|сохранить(?: изображение)?)$", re.I)
CHALLENGE = re.compile(r"verify you are human|checking your browser|just a moment|подтвердите, что вы человек", re.I)
QUOTA = re.compile(r"(?:you.ve (?:hit|reached)|you have reached).{0,80}limit|image generation limit|too many requests|limit resets|достигнут.{0,40}лимит|лимит.{0,40}достигнут", re.I | re.S)
REFUSAL = re.compile(r"(?:cannot|can.t|unable to) (?:help (?:with|create)|generate|create this image)|content policy|не могу (?:создать|сгенерировать)", re.I)


class BrowserError(RuntimeError):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def default_profile() -> Path:
    return Path(os.environ.get("DRAW_BROWSER_PROFILE") or "~/.config/draw-cli/chatgpt-browser").expanduser().absolute()


def prepare_profile(path: Path) -> Path:
    """Only adopt an empty directory or a profile previously created by draw."""
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise BrowserError("Browser profile must not be a symlink.")
    if path.exists():
        info = path.stat()
        if not path.is_dir() or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
            raise BrowserError("Browser profile must be a dedicated directory owned by you.")
        marker = path / MARKER
        if any(path.iterdir()):
            if marker.is_symlink() or not marker.is_file() or marker.stat().st_nlink != 1:
                raise BrowserError("Use an empty dedicated browser profile; draw never adopts personal Chrome or Desktop storage.")
            if marker.stat().st_size > 100 or marker.read_text() != MARKER_CONTENT:
                raise BrowserError("Unrecognized dedicated browser profile marker.")
    else:
        path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)
    marker = path / MARKER
    if not marker.exists():
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(MARKER_CONTENT)
    return path


@contextmanager
def profile_lock(profile: Path):
    """Serialize our own browser without killing another process or removing locks."""
    try:
        import fcntl
    except ImportError:
        raise BrowserError("The browser backend currently supports macOS and Linux.") from None
    fd = os.open(profile / ".draw.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BrowserError("Unsafe browser lock file.")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BrowserError("This browser profile is already in use by draw; finish that command first.") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def save_download(source: Path, target: Path) -> None:
    """Decode the fresh download and atomically encode the requested real format."""
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_IMAGE_BYTES:
        raise BrowserError("Downloaded image must be one regular file of at most 32 MiB.")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        current = os.fstat(stream.fileno())
        if (current.st_dev, current.st_ino, current.st_nlink) != (info.st_dev, info.st_ino, 1):
            raise BrowserError("Downloaded file changed before validation.")
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise BrowserError("Downloaded image exceeds 32 MiB.")
    try:
        with _image(data) as image:
            if min(image.size) < 256 or max(image.size) > 16384:
                raise BrowserError("Downloaded image dimensions are invalid.")
        _save(data, target)
    except CodexError as exc:
        raise BrowserError(str(exc)) from None


def visible(locator) -> bool:
    return any(locator.nth(i).is_visible() for i in range(locator.count()))


def readiness(page) -> str:
    if CHALLENGE.search(page.title()) or visible(page.get_by_text(CHALLENGE)):
        return "challenge_required"
    location = urlsplit(page.url)
    if location.scheme != "https" or location.netloc != "chatgpt.com":
        return "signin_required"
    login = re.compile(r"^(log in|sign in|войти)$", re.I)
    if visible(page.get_by_role("button", name=login)) or visible(page.get_by_role("link", name=login)):
        return "signin_required"
    if visible(page.locator(COMPOSER)) and visible(page.locator(ACCOUNT)):
        return "ready"
    return "loading"


def wait_ready(page, *, interactive: bool, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    notice = False
    state = "loading"
    while time.monotonic() < deadline:
        if page.is_closed():
            raise BrowserError("Login browser was closed; run `draw login` again.", 3)
        state = readiness(page)
        if state == "ready":
            return
        if state == "challenge_required":
            if not interactive:
                raise BrowserError("ChatGPT requires a human browser check. Run `draw login`, or explicitly use --headed and complete it yourself; draw does not bypass it.", 4)
            if not notice:
                print("draw: complete the browser check and sign-in yourself in the opened window.", file=sys.stderr)
                notice = True
        if state == "signin_required" and not interactive:
            raise BrowserError("ChatGPT browser sign-in required. Run `draw login`; a Desktop/Codex login is separate.", 3)
        page.wait_for_timeout(500)
    raise BrowserError("Sign-in was not confirmed. Run `draw login`; no image was requested.", 3)


def response_failure(text: str) -> str:
    if QUOTA.search(text):
        return "ChatGPT image usage limit reached; wait for the reset shown in ChatGPT."
    if REFUSAL.search(text):
        return "ChatGPT declined the image request; inspect the response in ChatGPT."
    return ""


def download_image(page, prompt: str, destination: Path, timeout: float, *, attached: bool = False) -> None:
    """Submit once, then only observe that new response and its download button."""
    composer = page.locator(COMPOSER)
    send = page.locator('[data-testid="send-button"]')
    if composer.count() != 1:
        raise BrowserError("ChatGPT composer changed; refusing blind input.")
    if page.locator('[data-message-author-role="assistant"]').count():
        raise BrowserError("Expected a new conversation; refusing to modify existing chat content.")
    composer.fill("Create exactly one image using ChatGPT image generation. Return the image, not a description.\n\n" + prompt)
    # ChatGPT may render Send only after the composer contains text.
    send.wait_for(state="visible", timeout=15000)
    if send.count() != 1:
        raise BrowserError("ChatGPT send control changed; refusing blind input.")
    send.click()
    print("draw: image request submitted once; waiting for the original image.", file=sys.stderr, flush=True)
    deadline = time.monotonic() + timeout
    image = None
    response = None
    while time.monotonic() < deadline:
        state = readiness(page)
        if state in ("challenge_required", "signin_required"):
            raise BrowserError("ChatGPT requires sign-in or a human check. Run `draw login` before another attempt.", 3)
        response = page.locator('[data-message-author-role="assistant"]').last
        text = response.inner_text()[-4000:] if response.count() else ""
        alerts = page.get_by_role("alert")
        for i in range(min(alerts.count(), 4)):
            text += "\n" + alerts.nth(i).inner_text()[-1000:]
        failure = response_failure(text)
        if failure:
            raise BrowserError(failure)
        if response.count():
            images = response.locator(GENERATED)
            if images.count() > 1:
                raise BrowserError("ChatGPT produced multiple images; refusing to choose an arbitrary output.")
            if images.count() == 1 and not visible(page.locator('[data-testid="stop-button"]')):
                if images.first.evaluate("img => img.complete && img.naturalWidth >= 256 && img.naturalHeight >= 256"):
                    image = images.first
                    break
        page.wait_for_timeout(1000)
    if image is None or response is None:
        raise BrowserError("No unambiguous generated image before timeout; inspect the chat before retrying.")
    image.hover()
    button = response.get_by_role("button", name=DOWNLOAD)
    if button.count() != 1:
        image.click()
        button = page.get_by_role("dialog").get_by_role("button", name=DOWNLOAD)
    if button.count() != 1:
        raise BrowserError("Original-image download control changed; no screenshot or private-API fallback was used.")
    if attached:
        from draw_cli.ui_download import download_original_link
        download_original_link(page, button, destination)
        return
    with page.expect_download(timeout=30000) as event:
        button.click()
    download = event.value
    if download.failure():
        raise BrowserError("Original-image download failed.")
    download.save_as(str(destination))


def run(*, mode: str, prompt=None, out_path=None, profile=None,
        headed: bool = False, channel: str = "chrome", timeout: float = 600,
        cdp_url=None, cdp_context=None) -> int:
    """Public CLI boundary; importing this module never launches a browser."""
    try:
        if mode not in ("login", "check", "generate") or not math.isfinite(timeout) or not 1 <= timeout <= 3600:
            raise BrowserError("Invalid browser mode or timeout; use 1..3600 seconds.")
        if channel not in ("chrome", "chromium"):
            raise BrowserError("Browser channel must be chrome or chromium.")
        if cdp_url:
            from draw_cli.app_session import checked_endpoint
            checked_endpoint(cdp_url)
            if mode == "login" or headed:
                raise BrowserError("CDP reuses an existing app; log in and complete human checks in that app yourself.")
        target = None
        if mode == "generate":
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
                raise BrowserError("Prompt must be nonempty UTF-8 text of at most 1 MiB.")
            if not out_path:
                raise BrowserError("An output image path is required.")
            target = Path(out_path).expanduser().absolute()
            if target.suffix.lower() not in FORMATS:
                raise BrowserError("Use a .png, .jpg, .jpeg or .webp output path.")
            if target.is_symlink() or (target.exists() and not target.is_file()) or not target.parent.is_dir():
                raise BrowserError("Output must be a regular file in an existing directory, not a symlink.")
            with tempfile.TemporaryFile(dir=target.parent):
                pass
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserError("Browser dependency missing. Install draw-cli[browser] in your draw environment; see docs/chatgpt-browser.md.") from None
        with sync_playwright() as playwright, browser_context(
                playwright, profile=profile, channel=channel, headed=headed or mode == "login",
                cdp_url=cdp_url, cdp_context=cdp_context) as context:
            page = context.new_page()
            try:
                page.set_default_timeout(15000)
                if mode == "login":
                    print("draw: sign in to ChatGPT in the opened browser. Passwords, MFA and human checks stay in that window.", file=sys.stderr, flush=True)
                page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
                wait_ready(page, interactive=mode == "login" or headed, timeout=timeout if mode == "login" or headed else min(timeout, 30))
                if mode in ("login", "check"):
                    print("draw: ChatGPT browser session ready; no image requested and image quota not verified.")
                    return 0
                print("draw: using ChatGPT web UI; no Codex or API key. Image model is service-managed.", file=sys.stderr)
                with tempfile.TemporaryDirectory(prefix="draw-chatgpt-web-") as scratch:
                    download = Path(scratch) / "original"
                    download_image(page, prompt, download, timeout, attached=bool(cdp_url))
                    save_download(download, target)
                print(f"draw: saved {out_path} (ChatGPT browser)")
                return 0
            finally:
                # Never operate on pre-existing user tabs, even in an attached app.
                if not page.is_closed():
                    page.close()
    except KeyboardInterrupt:
        print("draw: cancelled; no automatic retry. A submitted request may still consume usage.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"draw: {_diagnostic(str(exc))} No automatic retry or API fallback.", file=sys.stderr)
        return exc.code if isinstance(exc, BrowserError) else 1


@contextmanager
def browser_context(playwright, *, profile=None, channel="chrome", headed=False,
                    cdp_url=None, cdp_context=None):
    if cdp_url:
        from draw_cli.app_session import attached_context
        with tempfile.TemporaryDirectory(prefix="draw-attached-") as scratch:
            with attached_context(playwright, cdp_url, artifacts=Path(scratch),
                                  context_index=cdp_context) as context:
                yield context
        return
    directory = prepare_profile(Path(profile) if profile else default_profile())
    with profile_lock(directory):
        options = dict(headless=not headed, accept_downloads=True)
        if channel == "chrome":
            options["channel"] = "chrome"
        context = playwright.chromium.launch_persistent_context(str(directory), **options)
        try:
            yield context
        finally:
            context.close()  # Only draw's own dedicated browser, not an attached app.
