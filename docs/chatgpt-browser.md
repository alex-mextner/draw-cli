# Direct ChatGPT browser backend

## Purpose

`draw --backend chatgpt-web` submits an image request to the ChatGPT website and
saves the original image downloaded through its visible download control. It
never starts Codex or uses an API key. The existing `chatgpt`/`codex` backend still
uses the Codex allowance; a Codex quota error is not an authentication failure.
No backend is selected as an automatic fallback after a service denies a request.

The browser adapter is experimental and is not an official subscription Images
API. ChatGPT and the public API platform have separate billing. Nothing here
changes plan limits, creates credits, or pins an image model version.

## Install and use

Python 3.9+, macOS/Linux, the `[browser]` extra, and Google Chrome are required.
From the checkout (or use `pipx install --force '.[browser]'`):

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[browser]'
.venv/bin/draw login
.venv/bin/draw --backend chatgpt-web --check
.venv/bin/draw --backend chatgpt-web 'airplane made by apple' -o airplane.jpg
```

With an installed `draw` entry point, omit `.venv/bin/`.

```bash
draw login
draw --backend chatgpt-web 'airplane made by apple' -o airplane.jpg
printf '%s' 'a tiny astronaut cat' | draw --backend chatgpt-web -o cat.webp
DRAW_BACKEND=chatgpt-web draw 'a minimalist poster' -o poster.png
draw --backend chatgpt-web --headed 'a minimalist poster' -o poster.png
```

Login is always headed. Passwords, MFA and human checks are completed by the user
in the browser; they are never accepted as CLI arguments. An existing authenticated
draw profile is reused. Missing/expired sign-in exits with guidance to run `draw
login`. `--check` opens the profile without requesting an image; a successful check
does not establish available image quota or prove that current download selectors work.

Google Chrome uses its normal installed binary but a separate profile. To use
Playwright Chromium, install it explicitly in the same environment:

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/draw login --browser-channel chromium
.venv/bin/draw --backend chatgpt-web --browser-channel chromium 'a cat' -o cat.png
```

## Session storage and boundaries

The default directory is `~/.config/draw-cli/chatgpt-browser`. It is mode 0700 and
marked as owned by draw. Browser cookies and storage stay in this directory under
the browser's control. No `storage_state` export, cookie reading, token extraction,
Desktop Keychain access, or private ChatGPT HTTP calls are implemented.

`--browser-profile` / `DRAW_BROWSER_PROFILE` may select an empty dedicated directory
or one previously initialized by draw. Nonempty unrelated profiles and symlinks
are rejected. A per-profile file lock prevents concurrent draw sessions. The tool
never removes another process's browser locks or kills a personal Chrome session.
Only its own dedicated browser is closed when a command finishes.

Keep the profile private and out of source control, shared folders and artifacts.
The profile may contain sensitive session data even though draw never exports it.
`DRAW_BROWSER_CHANNEL` controls the default engine (`chrome` or `chromium`).

## Output and failure behavior

One new conversation, one submission, and one unambiguous generated image are
required. Only generated-image elements in that response are considered. The
original download must decode as an image, be at most 32 MiB, and have dimensions
between 256 and 16384 pixels per side. No screenshot or stale local image is used.

PNG, JPEG and WebP are actually encoded in the requested format. JPEG transparency
is composited onto white. Atomic replacement preserves a previous output if login,
generation, downloading or image validation fails. Prompt input is limited to
1 MiB of UTF-8. `--timeout` accepts 1..3600 seconds (default 600); browser startup,
readiness and downloading have separate bounded waits.

Reference images, model selection, HF flags and Codex flags are rejected for this
initial browser version rather than being silently ignored. Use the existing
Codex backend explicitly for its reference-image workflow.

Service limits and refusals stop the operation. Headless human checks exit with code 4;
`draw login` and explicitly headed commands wait for the user to complete them. No
automatic request retry, paid API fallback, proxy rotation, stealth or challenge
bypass is implemented. A submitted request may consume usage even if its download
fails or the local command is cancelled. Inspect the chat before retrying.

Exit codes: 0 success; 1 generation/runtime error; 2 invalid CLI syntax; 3 sign-in
required/not confirmed; 4 human check required; 130 user cancellation.

## Tests and upstream references

The standard suite is offline. The additional browser contract uses real Chrome
with every request intercepted by a local DOM fixture; it does not contact ChatGPT:

```bash
DRAW_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_chatgpt_web_browser.py -q
```

Observed on this Mac on September 19, 2026: a headed login succeeded, but a later
headless navigation received a title-only "Just a moment..." browser check. This
is not a missing login and must not be treated as proof that sign-in failed.
Headless operation is therefore not guaranteed after a successful login. Use
`--headed` to interact with the page normally; do not automate challenge solving.

A live ChatGPT generation requires a user-authorized login and is separate evidence
from these mocked-browser tests. Do not describe fixture output as a real generated image.

- https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan
- https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform
- https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context
- https://playwright.dev/python/docs/downloads

## Verification record: September 19, 2026

- Baseline: 253 passed, 2 opt-in live Codex tests skipped.
- Final suite including real Chrome against intercepted local DOM fixtures:
  295 passed, 2 opt-in live Codex tests skipped. These are not live image results.
- Wheel build, packaged source-byte comparisons, CLI help/version and diff checks passed.
- Installed the 0.6.0 wheel into an isolated runtime; the public draw command was
  switched atomically. The previous symlink target is recorded with the runtime.
- A headed ChatGPT login reached the authenticated-UI readiness check successfully.
- A subsequent headless request encountered a title-only browser verification page.
  Explicit headed mode also waited for human verification. No image submission was
  logged, and no live generated output was produced. The test browser and its owned
  driver were stopped; no background retry remains.
- A real generation/download is still unverified. This integration remains experimental;
  passing the local browser contract must not be presented as complete live compatibility.
