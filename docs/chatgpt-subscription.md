# ChatGPT plan images through Codex CLI

`draw --backend chatgpt` delegates image creation/editing to the **official installed Codex CLI** while Codex is signed in with ChatGPT. `--backend codex` is an alias.

This path deliberately does **not**:

- use an OpenAI API key;
- call an unofficial ChatGPT endpoint;
- extract browser cookies or copy Codex authentication files;
- automate ChatGPT Desktop;
- silently fall back to Hugging Face or a separately billed OpenAI API request.

ChatGPT Desktop is therefore optional. The integration point is the Codex CLI.

## Setup on macOS

```bash
npm install -g @openai/codex@latest
codex login
codex login status

pipx install --force git+https://github.com/alex-mextner/draw-cli
draw install-skill
draw --backend chatgpt --check
```

Use **ChatGPT login**, not API-key login. If the CLI is currently configured for an API key, switch it yourself with `codex logout` and `codex login`. `draw` never changes the account or login method on the user's behalf.

OpenAI documents that Codex is available through ChatGPT plans and that signing in with ChatGPT uses plan usage, while using an API key uses API pricing: <https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan>.

`--check` is intentionally local. It checks:

1. a Codex executable exists;
2. `codex --version` works;
3. `codex login status` reports ChatGPT login and not API-key login;
4. `codex exec --help` exposes every flag the adapter depends on;
5. `codex features list` advertises `image_generation`.

It does **not** submit an image request, prove current quota, or attest which server-side image checkpoint the account will receive.

## Generate and edit

```bash
draw "Кот-космонавт, акварель, белый фон" --backend chatgpt -o cat.png

printf '%s\n' 'Minimal abstract gradient, square composition' \
  | draw --backend chatgpt -o art.png

draw "Сохрани композицию, замени фон на кремовый" \
  --backend chatgpt -i source.png -o edited.png

draw "Combine these references into one composition" \
  --backend chatgpt -i first.png -i second.jpg -o combined.webp
```

Maximums enforced by `draw`:

- prompt: **1 MiB UTF-8**;
- references: **5**;
- each reference: **32 MiB**;
- native output artifact: **32 MiB**.

Use `.png` when you want the native PNG bytes preserved. JPEG/WebP outputs are re-encoded; JPEG alpha is flattened onto white. Existing output files are replaced atomically only after the result validates successfully. A failed generation leaves the existing output unchanged.

## Exact flow

1. `draw` validates the prompt, output path and all explicit reference images **before starting Codex**.
2. The adapter runs the local Codex preflight described above.
3. A fresh temporary workspace is created.
4. References are re-read, validated again, and encoded as workspace-local PNG files.
5. `codex exec` is launched without a shell. The user prompt is supplied on stdin.
6. The child is forced to the OpenAI provider + ChatGPT login method for this invocation. User config and execpolicy rules are ignored; project instruction bytes are disabled; shell/web/agent/app features are disabled; the shell sandbox is read-only and approval policy is `never`.
7. The Codex reasoning model invokes the native image-generation extension.
8. `draw` parses JSONL events and requires one valid canonical UUID from `thread.started` plus `turn.completed`.
9. The result must be exactly one native PNG in either:
   - `CODEX_HOME/generated_images/<thread-id>/`, or
   - the fresh workspace's `generated_images/` directory on executor-backed clients.
10. The file must be a regular, non-hardlinked, non-symlinked file within the expected directory, at most 32 MiB, and decode as a valid image.
11. Only then is the requested output atomically replaced.

The artifact lookup intentionally does **not** use wall-clock/mtime freshness. The `CODEX_HOME` path is already scoped by the canonical thread UUID returned by the current execution, while the alternate workspace directory is newly created and empty before the run. Depending on mutable wall-clock timestamps caused false negatives after clock changes or restored metadata without adding meaningful isolation.

The adapter never searches all of `CODEX_HOME` for the newest image and never trusts a path written in the model's final text.

## Child process and authentication boundary

The existing `HOME` and `CODEX_HOME` remain available so the official Codex client can use its normal ChatGPT authentication storage/keychain behavior. `draw` itself never reads or copies `auth.json` or OAuth tokens.

Before launching the child, it removes OpenAI/Azure/HF API-key-style environment overrides such as `OPENAI_*`, `AZURE_OPENAI_*`, `CODEX_API_KEY`, and `HF_TOKEN`. That prevents an unrelated shell configuration from accidentally turning this route into API-key billing. The parent process environment is not modified.

Diagnostics are bounded and redact common bearer/JWT/key forms plus assignment-style `api_key`, `access_token`, `refresh_token`, OpenAI, Codex and HF secrets.

The Codex executable itself is part of the trusted local computing base. These controls are defense-in-depth against accidental tool/config inheritance and prompt-induced behavior; they are not a sandbox against a malicious replacement `codex` binary running with the user's OS permissions.

## ChatGPT Images 2.5 is a rollout, not a CLI model selector

OpenAI announced ChatGPT Images 2.5 on **September 8, 2026** and states that the rollout includes Codex users. See:

- <https://openai.com/index/introducing-chatgpt-images-2-5/>
- <https://help.openai.com/en/articles/6825453>

The public API additionally exposes GPT Image 2.5 Flare/Sunburst model names. The native Codex image tool is different: as reviewed on September 9, 2026, its tool arguments contain prompt/reference inputs but no image-model selector, while the client source still contains the internal request identifier `gpt-image-2`.

Therefore `draw` cannot honestly guarantee or pin `GPT-Image-2.5 Flare` or `Sunburst` for subscription usage. It invokes whatever native ChatGPT Images backend OpenAI exposes to that signed-in Codex account during rollout.

`--codex-model` / `DRAW_CODEX_MODEL` select the **reasoning model** that calls the image tool. They do not select the image generator. `--model` remains Hugging Face-only.

Current upstream sources reviewed:

- Codex exec CLI flags: <https://github.com/openai/codex/blob/main/codex-rs/exec/src/cli.rs>
- Native image tool: <https://github.com/openai/codex/blob/main/codex-rs/ext/image-generation/src/tool.rs>
- Native image artifact layout: <https://github.com/openai/codex/blob/main/codex-rs/ext/image-generation/src/artifact.rs>
- Exec JSONL event types: <https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs>

## Failures, quota and cancellation

The adapter performs **zero automatic image-generation retries**. A retry can consume plan allowance twice, so retry decisions belong to the caller/user.

The default generation timeout is 600 seconds. `--timeout 900` changes the generation timeout only. Preflight commands use their own short timeout. `--timeout` is rejected with `--check` so the CLI never implies it affected a check when it did not.

On timeout or cancellation, the local Codex process group is terminated on POSIX systems. A request that already reached OpenAI may still have consumed allowance; terminating a local process cannot reverse server-side usage.

Native Codex artifacts are left in Codex's own generated-image cache. `draw` only copies/encodes the selected result to the requested destination.

## Testing

Offline suite:

```bash
python -m pip install 'pytest>=8,<9' Pillow
python -m pytest tests/ -q
```

The fake executable tests exercise real subprocess boundaries but never use a real account, credentials, or paid request. CI runs Python 3.9 and 3.12 on Linux and a separate macOS adapter job.

Optional live preflight (uses your real local Codex login but does not generate):

```bash
DRAW_LIVE_CODEX=1 python -m pytest tests/test_codex_live.py -q
```

Optional live generation (explicit because it may consume plan usage):

```bash
DRAW_LIVE_CODEX_GENERATE=1 python -m pytest tests/test_codex_live.py -q
```

The live generation test is never enabled by repository CI.

The RED→GREEN adversarial cases and remaining accepted risks are tracked in [adversarial-review.md](adversarial-review.md).
