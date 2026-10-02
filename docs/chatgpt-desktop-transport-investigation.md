# ChatGPT Desktop transport investigation — September 19, 2026

## Requested behavior

Use draw from the Remote Desktop Commander plugin to generate several images while
the calling ChatGPT conversation does other work. Use the ChatGPT Images entitlement,
not the Codex allowance or a separately billed Platform API. The desired transport is
a direct authenticated API, with a session owned by the user's ChatGPT desktop app.
Browser automation can satisfy the concurrent-image workflow. It is a different
transport from direct API calls; the earlier wording incorrectly conflated those
two questions. Reusing an already authenticated app/browser session does not
require exporting its credentials.

## Confirmed locally

- The old `chatgpt` and `codex` selectors both use `draw_cli.codex.generate`.
  There is no transport or entitlement difference. Version 0.6.1 makes `codex`
  canonical and prints a deprecation warning for the legacy `chatgpt` alias;
  existing invocations are not silently rerouted.
- The installed application bundle is `/Applications/ChatGPT.app`.
  Its static Info.plist reports version `26.908.70816`, build `9275`, and bundle ID
  `com.openai.codex`. The package name in app.asar is `openai-codex-electron`.
- The archive index contains separate ChatGPT request, conversation, image-viewer,
  desktop-auth-URL and auth-handoff modules. These filenames indicate distinct
  application components; they do not establish API contracts, token scope,
  billing rules, or an externally usable image-generation interface.
- Reading the selected request/auth/preload module source was blocked by the tool's
  safety check with an indeterminate-safety result. No alternate extraction route
  was attempted. No credentials, Keychain contents or browser session files were read.

## Documented interfaces and remaining gap

OpenAI's Codex App Server documents experimental `chatgptAuthTokens` login. It is for
host applications that already own the user's auth lifecycle and supply/refresh the
token. It authenticates Codex App Server; it does not establish a separate ChatGPT
Images transport or remove Codex usage accounting.

The authentication guide documents Codex-managed cached login and OS credential
storage. That is not evidence about the unread ChatGPT-specific request modules.
Separate Platform billing does not, by itself, prove that automating the desktop
application protocol is technically impossible.

No direct ChatGPT image request was sent during this investigation. No working
Desktop-session-to-image backend was implemented, and no live image was generated.
Do not present the alias warning, mocked tests, or browser backend as satisfying the
requested direct subscription API or parallel multi-image workflow.

References checked on September 19, 2026:
- https://developers.openai.com/codex/app-server/
- https://developers.openai.com/codex/auth/
- https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform


## Session reuse follow-up

The shipped Chromium scripting dictionary and Info.plist declare AppleScript
support. A read-only `count of windows` request timed out with -1712. This does
not prove that every automation interface is unsupported, nor that a login is missing.
The running ChatGPT app did not expose a TCP listener for browser automation.

The new `draw app-session` command writes a fixed-schema, credentials-free report
under a new private `/tmp/draw-app-report-*/` directory. `--launch` is an explicit
normal launch of the CLOSED app with a loopback debugging port. It refuses to
restart an existing app, does not patch binaries or permissions, and stops if the
app does not expose the interface. The app continues to own its login.

`draw --backend chatgpt-web --cdp-url http://127.0.0.1:9236` attaches to that
explicitly enabled session. It creates only its own tab and does not launch a
second profile or close existing tabs/the app. It uses Playwright's no_defaults
option so existing download and emulation settings are not changed globally.
The download is the original link emitted by the UI download button, not an img
preview or a screenshot. Its bytes remain inside the page until transferred as
image data; no auth files, cookies, storage values or request headers are exported.

Independent draw processes can attach concurrently and own separate tabs. This
avoids the single-profile process lock of the original launch-per-image adapter.
It does not increase service concurrency limits or automatically retry denials.
No daemon, persistent retry loop or scheduled task was started by this change.

A control-port connection is NOT proof that a new web tab inherits the native
app's authenticated ChatGPT session. --probe-web-session tests that separately;
image_generation_verified remains false until a real generation succeeds.
A native app may use a different browser context or a different auth mechanism.
Direct extraction of Desktop credentials and a standalone private Images API
client remain unimplemented. The manual script is not an alternate extractor.
