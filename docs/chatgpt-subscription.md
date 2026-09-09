# ChatGPT subscription images (Codex CLI)

`draw --provider chatgpt` delegates generation to the **official installed Codex CLI**,
logged in using ChatGPT. It does not use an OpenAI API key, call an unofficial endpoint,
extract browser cookies, read/copy authentication tokens, or automate desktop windows.
`--provider codex` is an alias. ChatGPT Desktop is not required; installing Desktop
alone does not provide this command-line integration.

## Setup on macOS

Install or update Codex, then sign in with the account whose subscription you use:

```bash
npm install -g @openai/codex@latest
codex login
codex login status

# Install/update draw from main after this feature is merged.
pipx install --force git+https://github.com/alex-mextner/draw-cli
draw install-skill
draw --provider chatgpt --check
```

Choose **ChatGPT**, not API-key authentication. If Codex is already in API-key mode,
switch it yourself with `codex logout` followed by `codex login`. draw never performs
that switch for you. The existing `HOME`, `CODEX_HOME`, and Codex credential storage
remain in use. A custom binary can be selected with `--codex-bin /path/to/codex`.

`--check` checks the executable, login status, required CLI options and native
`image_generation` feature. It does not generate an image, contact the image service
to verify entitlement, or certify which image model your account will receive.
An old client is rejected before generation; update Codex instead of using an API fallback.
The adapter is not macOS-specific, but authenticated generation must be tested on your
own machine. CI is configured to exercise the offline subprocess contract on Linux and macOS.

## Generate and edit

```bash
draw "Кот-космонавт, акварель, белый фон" --provider chatgpt -o cat.png
printf 'Minimal abstract gradient, square composition' | draw --provider chatgpt -o art.png

draw "Сохрани композицию, замени фон на кремовый" \
  --provider chatgpt -i source.png -o edited.png

# Up to five explicitly supplied reference images; one output per invocation.
draw "Combine these references into one composition" \
  --provider chatgpt -i first.png -i second.jpg -o combined.webp
```

Use `.png` to retain the original image bytes and embedded metadata. JPEG/WebP outputs
are actually encoded in their selected format, not just renamed; JPEG flattens alpha
onto white. Re-encoding may discard original metadata. The output directory must already
exist. Existing outputs are replaced atomically **only after** a valid image is received;
errors leave them unchanged. Symlink destinations are rejected.

Set the default without changing existing HF scripts globally:

```bash
DRAW_PROVIDER=chatgpt draw "A soft geometric landscape" -o landscape.png
```

Or add this setting to `~/.config/draw-cli/.env` for all future draw invocations:

```dotenv
DRAW_PROVIDER=chatgpt
# Optional when codex is not on PATH:
# DRAW_CODEX_BIN=/opt/homebrew/bin/codex
```

Hugging Face remains the default without this setting. `HF_TOKEN` and `HF_MODEL` apply
only to the HF provider and are not required for subscription generation.

## GPT Image 2.5: availability is not a model pin

OpenAI's [Images 2.5 announcement](https://openai.com/index/introducing-chatgpt-images-2-5/)
on September 8, 2026 includes Codex in the rollout. **draw does not have an independent
switch that guarantees GPT Image 2.5, Flare, or Sunburst.** It uses whatever native image
backend Codex makes available to the signed-in account.

At implementation review on September 9, the inspected native Codex tool had no image
model argument and still used the internal request identifier `gpt-image-2`. The
announcement and this client identifier are not proof that every CLI invocation uses
the same server-side model. draw neither rewrites that identifier nor claims to verify
the server's model. Keep Codex updated; exact checkpoint selection is unsupported here.

`draw --model` remains **HF-only**. `--codex-model` / `DRAW_CODEX_MODEL` select the
**reasoning agent** that invokes the image tool, not an image model. Normally omit them.
Passing a GPT Image ID there is rejected rather than silently using another model.
A prompt saying “use 2.5” cannot enforce a model selection either.

## Usage limits and failures

The normal [Codex ChatGPT-plan rules](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
apply, including entitlement, quotas and any additional usage configured in your account.
This is not unlimited generation or an API-billing workaround. Both the Codex agent turn
and image generation can consume the account's applicable allowances.

The adapter never automatically retries generation or falls back to HF, an API-key skill,
or a separately billed OpenAI API. The agent is instructed to stop on refusal, tool
unavailability or quota exhaustion. If it returns text without a native image, draw
fails rather than accepting a fabricated file path or placeholder.

The default generation timeout is 600 seconds; change it with `--timeout 900`. Local
preflight commands have a separate 20-second timeout. Cancellation/timeout terminates
the local process group on macOS/Linux. A request already submitted to the service may
still finish and consume usage; killing the local process cannot undo it. Original native
artifacts are left in Codex's cache; they are not automatically deleted by draw.

## Implementation and verification

The child runs in a fresh temporary workspace, with only explicitly provided references
staged there. It uses read-only shell sandbox mode, disables shell tools and web search,
and ignores user config, project instruction documents and execpolicy rules for this run.
This avoids inheriting an alternate API provider or recursively invoking the installed
`draw` skill. It does not modify the user's Codex configuration or relax managed policy.
These controls are not a general-purpose security boundary against a malicious CLI binary.

The adapter obtains the current UUID from `thread.started` in `codex exec --json` and
requires a successful `turn.completed`. It reads exactly one validated native PNG from
`CODEX_HOME/generated_images/<thread_id>/`, with support for the fresh workspace's native
`generated_images/` directory. It never searches globally for the newest file, reuses an
old thread, or trusts an agent-authored output path. Linked, stale, oversized, corrupt
and ambiguous artifacts are rejected. Prompts go through stdin, not shell interpolation.

Upstream contracts inspected:

- [CLI reference](https://developers.openai.com/codex/cli/reference/): `exec --json`,
  `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, stdin prompts and image inputs.
- [Native tool and image arguments](https://github.com/openai/codex/blob/38cbebaf3fe3e81a94bf462079e7cf9659fc9e50/codex-rs/ext/image-generation/src/tool.rs).
- [Native artifact layout](https://github.com/openai/codex/blob/38cbebaf3fe3e81a94bf462079e7cf9659fc9e50/codex-rs/ext/image-generation/src/artifact.rs).
- [Exec event protocol](https://github.com/openai/codex/blob/38cbebaf3fe3e81a94bf462079e7cf9659fc9e50/codex-rs/exec/src/exec_events.rs).

Run offline adapter/CLI tests (fake executable; no credentials or paid requests):

```bash
python -m pip install 'pytest>=8,<9' Pillow
python -m pytest tests/test_codex.py tests/test_smoke.py tests/test_version.py -q
```

These tests verify the process/protocol contract and failure handling, **not** live model
availability, image quality or a completed generation on a user's subscription. The manual
acceptance test is `draw --provider chatgpt --check`, followed by one actual generation on
an authorized account and inspection of the resulting image.
