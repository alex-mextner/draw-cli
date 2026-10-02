"""Capture only an original file link emitted by one UI download action.

The generated image stays in its browser session. No auth data, request headers,
application storage or preview-image bytes are read. Only the original marked
`<a download>` link created by the download control is handled. No generation is
retried when a site uses an unsupported native download path.
"""
from __future__ import annotations
import base64
import uuid
from pathlib import Path

INSTALL = r"""({key, limit}) => {
  if (Object.prototype.hasOwnProperty.call(window, key)) throw new Error('Capture collision');
  const state = {status: 'waiting', data: null, size: 0};
  const originalClick = HTMLAnchorElement.prototype.click;
  let active = true;
  const controller = new AbortController();
  async function capture(anchor) {
    if (!active || !anchor.hasAttribute('download')) return false;
    if (state.status !== 'waiting') { state.status = 'multiple_downloads'; return true; }
    state.status = 'reading';
    try {
      const url = new URL(anchor.href, location.href);
      const same = url.origin === location.origin;
      const cdn = url.hostname === 'oaiusercontent.com' || url.hostname.endsWith('.oaiusercontent.com');
      const dataImage = /^data:image\/(png|jpeg|webp);base64,/i.test(anchor.href);
      if (!((url.protocol === 'blob:' && same) ||
            (url.protocol === 'https:' && (same || cdn)) || dataImage)) {
        state.status = 'unsupported_original_link'; return true;
      }
      const response = await fetch(url.href, {credentials: same ? 'same-origin' : 'omit', redirect: 'error', signal: controller.signal});
      if (!response.ok || !response.body) { state.status = 'original_unavailable'; return true; }
      const mime = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
      if (!['image/png', 'image/jpeg', 'image/webp', 'application/octet-stream'].includes(mime)) {
        state.status = 'not_an_original_image'; return true;
      }
      const reader = response.body.getReader();
      const chunks = []; let bytes = 0;
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        bytes += part.value.byteLength;
        if (bytes > limit) { await reader.cancel(); state.status = 'image_too_large'; return true; }
        chunks.push(part.value);
      }
      if (state.status !== 'reading') return true;
      const blob = new Blob(chunks, {type: 'application/octet-stream'});
      const data = await new Promise((resolve, reject) => {
        const file = new FileReader(); file.onload = () => resolve(file.result);
        file.onerror = reject; file.readAsDataURL(blob);
      });
      if (state.status !== 'reading') return true;
      state.data = data.substring(data.indexOf(',') + 1);
      state.size = bytes; state.status = 'complete';
    } catch (_) { state.status = 'original_download_failed'; }
    return true;
  }
  function handler(event) {
    const target = event.target instanceof Element ? event.target.closest('a[download]') : null;
    if (target && active) { event.preventDefault(); void capture(target); }
  }
  function click() {
    if (active && this.hasAttribute('download')) { void capture(this); return; }
    return originalClick.apply(this, arguments);
  }
  HTMLAnchorElement.prototype.click = click;
  document.addEventListener('click', handler, true);
  state.cleanup = () => {
    active = false;
    controller.abort();
    if (HTMLAnchorElement.prototype.click === click) HTMLAnchorElement.prototype.click = originalClick;
    document.removeEventListener('click', handler, true);
    delete window[key];
  };
  window[key] = state;
}"""


def download_original_link(page, button, destination: Path, *, timeout_ms=30000) -> None:
    from draw_cli.chatgpt_web import BrowserError
    from draw_cli.codex import MAX_IMAGE_BYTES
    key = "__draw_original_" + uuid.uuid4().hex
    page.evaluate(INSTALL, {"key": key, "limit": MAX_IMAGE_BYTES})
    try:
        button.click()  # Exactly once; unsupported native downloads are not clicked again.
        page.wait_for_function("key => window[key] && !['waiting','reading'].includes(window[key].status)",
                               arg=key, timeout=timeout_ms)
        result = page.evaluate("key => ({status:window[key].status, size:window[key].size, data:window[key].data})", key)
        if result.get("status") != "complete":
            raise BrowserError("The UI did not expose a supported original-image download. No preview or private API fallback.")
        encoded = result.get("data")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise BrowserError("Original-image download exceeded the size limit.")
        data = base64.b64decode(encoded, validate=True)
        if not 0 < len(data) <= MAX_IMAGE_BYTES or len(data) != result.get("size"):
            raise BrowserError("Invalid original-image download size.")
        with destination.open("xb") as stream:
            stream.write(data)
    finally:
        if not page.is_closed():
            page.evaluate("key => { if (window[key]) window[key].cleanup(); }", key)
