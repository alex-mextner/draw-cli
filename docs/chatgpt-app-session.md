# Reuse an existing ChatGPT app/browser session

## Goal and boundaries

The browser transport is suitable for running image jobs via Remote Desktop
Commander while the calling conversation performs other tool work. Transport
choice (browser UI versus a direct API) and scheduling (parallel local jobs) are
separate concerns. Neither implies additional service quota.

This implementation reuses the browser session in place. It does not extract
Desktop credentials, read Keychain or session storage, copy browser profiles,
export cookies, or bypass a login, human verification page or server limit.
It is not a standalone subscription Images API client.

## Inspect without changing the running app

```bash
draw app-session
```

The command reads public application metadata and app-owned listening sockets.
It saves a report to `/tmp/draw-app-report-<random>/report.json`. The containing
directory is mode 0700 and the JSON file is mode 0600. Reports contain fixed
status fields, app version and feature booleans, never tokens, cookie values,
page text, chat titles, full URLs, HAR archives or arbitrary server responses.

## Explicitly start the existing app session with local control

Save your work and quit ChatGPT yourself. Then run:

```bash
draw app-session --launch --probe-web-session
```

Equivalent repository helper:

```bash
scripts/chatgpt-app-session.sh
```

This starts `/Applications/ChatGPT.app` normally with its existing app data and
`--remote-debugging-port=9236 --remote-debugging-address=127.0.0.1`. It does not
restart a running application or create a separate login profile. If the app
rejects these standard debugging switches, the command reports failure; it does
not remove app protections or substitute another extraction method.

**Security:** a browser debugging endpoint permits broad control of that browser
session by other processes on the same machine. Use it only on a trusted local
machine. It must not be exposed through a tunnel, proxy, public interface or
Tailscale endpoint. The script verifies the listener is owned by ChatGPT and is
bound only to loopback before attaching. Quit the app when finished to close the
port; a later ordinary launch does not receive these flags from draw.

A successful probe means a NEW tab reached the signed-in ChatGPT UI. It does
not establish image quota, image model version or successful image generation.
If the app has multiple contexts, the report gives their count and the number
of existing ChatGPT-origin tabs in each, without their titles or URLs. Select
`--cdp-context N` explicitly to probe a particular one; draw does not guess.

## Generate in an attached session

Only after the session probe succeeds:

```bash
draw --backend chatgpt-web --cdp-url http://127.0.0.1:9236 --check
draw --backend chatgpt-web --cdp-url http://127.0.0.1:9236   'airplane made by apple' -o airplane.jpg
```

Multiple independent draw processes can connect to the same browser. Each uses
its own newly created tab. Remote Desktop Commander can return their process IDs
and read their output later in the same workflow instead of holding one call open.
Existing tabs are not closed or navigated, and disconnecting one job does not
close the app or another job's tab. No persistent background worker is required.

The `--cdp-url` option accepts only an explicitly supplied numeric loopback HTTP
endpoint with a port. Remote URLs, credentials, query strings, redirects and
cross-host WebSocket discovery are refused. It cannot be combined with profile,
channel or headed launch options: those describe launching a new browser, not
attaching to an existing one. --cdp-context requires --cdp-url.

No profile/session data is copied to a new browser. Consequently, a native app's
OAuth sign-in may not authenticate a new chatgpt.com tab. A successful CDP
connection with signin_required is a real distinction, not a reason to export
its token automatically. No no-login guarantee is made until this app is tested.

## Download behavior

The adapter preserves global download settings (`no_defaults=True`). In the
attached tab only, it captures a marked download link produced by exactly one
click on the response's original-image download control. It reads that original
file inside the browser session and transfers image bytes to draw. It never
reads the visible preview as a substitute, and never repeats image generation.
Temporary page handlers are restored even on failure. Native save dialogs and
other unsupported download mechanisms fail explicitly.

Only same-origin/blob image links, image data URLs, or HTTPS oaiusercontent.com
image CDN links are accepted. External CDN fetches omit credentials, redirects
are refused, and image bytes are bounded to 32 MiB and validated before atomically
encoding the requested PNG/JPEG/WebP output. A previous output survives failure.

## Verification scope

Local Chrome tests use temporary fixture profiles and intercepted test pages,
not the user's ChatGPT session. They verify independent simultaneous attachments,
app/tab survival after disconnect, one submission, download-handler cleanup,
rejection of unrelated links and preservation of original image resolution when
the displayed preview is smaller. A live app generation is a separate test and
has not succeeded in the recorded investigation.

Primary interface references:
- https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp
- https://playwright.dev/python/docs/api/class-browser#browser-close
- https://www.electronjs.org/docs/latest/api/command-line-switches


### Local verification record (September 19, 2026)

The full suite with the opt-in local Chrome contracts reports 329 passed and 2
live Codex tests skipped. Two independent CDP clients were open simultaneously;
closing one preserved the other client's owned tab and the original browser tabs.
The original-download fixture was 512x384 while its displayed preview was 256x256;
the saved JPEG retained the original 512x384 dimensions. Unsupported links and
missing download controls failed without a second click or output replacement.

The real app report currently says app_control_unavailable: ChatGPT is running
without an enabled control listener. No app restart, credential read, live prompt
submission, or successful generated image is claimed. A manual normal app launch
with --probe-web-session is required before this installed app can be validated.
