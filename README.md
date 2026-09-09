# draw-cli

Generate images from text prompts via **Hugging Face** or a **ChatGPT subscription through
the installed Codex CLI** — from any shell, script, or AI coding agent.

## ChatGPT subscription (no API key)

```bash
npm install -g @openai/codex@latest
codex login                         # choose ChatGPT, not API-key login
draw --provider chatgpt --check      # local setup check; no image generated
draw "a cute robot" --provider chatgpt -o robot.png
```

ChatGPT Desktop is not required. The adapter uses native Codex image generation,
not an API-key imagegen skill, scraped credentials or window automation. It stops
on errors instead of falling back to a separately billed API. Plan limits still apply.

**GPT Image 2.5:** OpenAI includes Codex in the Images 2.5 rollout, but the image model
is managed by Codex. draw cannot independently pin or verify 2.5 / Flare / Sunburst.
Do not pass image-model IDs to `--model` or `--codex-model`.
See [setup, image editing, limits and model details](docs/chatgpt-subscription.md).

Add `DRAW_PROVIDER=chatgpt` to `~/.config/draw-cli/.env` to make this the default.
Without it, the existing Hugging Face default and commands are unchanged.

## Why agents use this

A coding agent can generate placeholder art, hero images, mock UI assets, or concept sketches
inline from its shell session, without switching context or calling a web API manually.

```bash
# Generate a placeholder hero image for a landing page being built
draw "minimalist SaaS dashboard hero, dark theme, 16:9" -o assets/hero.png

# Produce an icon concept during component work
draw "flat vector icon, a glowing terminal cursor, transparent background" -o src/icons/cursor.png

# Pipe a dynamically assembled prompt from another tool
echo "isometric 3D render of a microservice architecture diagram, pastel colors" | draw -o docs/arch.png
```

The output is a plain image file — drop it straight into the asset pipeline, send it to Figma,
or attach it to a Telegram report via `tg --photo`.

## Install

draw installs `huggingface_hub` (HF) + `Pillow` (both providers); the recommended path is **pipx** —
an isolated venv with the deps and `draw` on your PATH:

```bash
pipx install git+https://github.com/alex-mextner/draw-cli
```

**One-liner** (pipx-first: uses pipx if present, else symlinks `draw` into PATH and installs
deps with `pip --user`; either way registers the agent skill):

```bash
curl -fsSL https://raw.githubusercontent.com/alex-mextner/draw-cli/main/install.sh | bash
```

Finish with HF token setup or ChatGPT login (above), then run the skill registration step
manually if you installed with raw pipx (the one-liner runs it for you):

```bash
draw install-skill
```

`install-skill` is idempotent — it writes a skill file to `~/.agents/skills/draw/` so Claude
Code, Codex, opencode, and Gemini harnesses know `draw` exists. The one-liner runs it
automatically.

### Token setup (Hugging Face only)

Skip this for `--provider chatgpt`. Create `~/.config/draw-cli/.env`:

```
HF_TOKEN=hf_...
```

Get a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
(a free-tier read token is enough for inference).

## Usage

```bash
# Prompt as positional arg
draw "a cute robot" -o robot.png

# Override model
draw "a cute robot" --model black-forest-labs/FLUX.1-dev -o robot.png

# Prompt from stdin
echo "a cute robot" | draw -o robot.png

# Print version and exit (no -o needed)
draw --version
```

## Flags

| Flag | Default | Description |
|------|---------|-------------|
| `prompt` (positional) | — | Text prompt. Reads from stdin if omitted. |
| `-o / --out` | required except `--check` | Output image path (e.g. `out.png`). |
| `--provider` | `hf` / `DRAW_PROVIDER` | `hf`, `chatgpt`, or `codex` (alias for `chatgpt`). |
| `--model <hf-id>` | `black-forest-labs/FLUX.1-schnell` | Any HF text-to-image model ID. |
| `-i / --image` | — | Reference/edit image, repeat up to five times (ChatGPT only). |
| `--check` | — | Check local Codex/login/capabilities without generating (ChatGPT only). |
| `--codex-bin` | `codex` | Path/name of the installed Codex executable. |
| `--codex-model` | Codex default | Reasoning agent model, **not** an image model; normally omit. |
| `--timeout` | `600` | Codex generation timeout in seconds; no automatic retry. |
| `-V / --version` | — | Print the version (`draw <ver>`) and exit. Works without `-o`. |

## Env vars

| Variable | Description |
|----------|-------------|
| `HF_TOKEN` | Hugging Face access token. Auto-loaded from `~/.config/draw-cli/.env`. |
| `HF_MODEL` | HF-only default model override (same effect as `--model`). |
| `DRAW_PROVIDER` | Default provider: `hf`, `chatgpt`, or `codex`. |
| `DRAW_CODEX_BIN` | Codex executable path/name. |
| `DRAW_CODEX_MODEL` | Optional Codex reasoning model, not the image model. |
| `CODEX_HOME` | Existing Codex home/authentication location (defaults to `~/.codex`). |

## Requirements

- Python 3.9+
- For ChatGPT: a current Codex CLI, ChatGPT login and native image-generation access
- [`huggingface_hub`](https://pypi.org/project/huggingface_hub/) and `Pillow` Python packages

---

## How draw compares

The other text-to-image CLIs trade off between *simple-but-locked-in* and
*powerful-but-heavy*. Single-vendor tools (dallecli, openai-cli-art) are one `pip install`
but hard-wired to OpenAI and a paid key. Model runners (Replicate CLI, simonw `llm`) are
flexible but route through a paid hosted API or a general LLM harness. Local engines
(comfy-cli / ComfyUI) are the most capable but pull in a full generative stack and a server.

`draw` is the minimal middle: **one command**, **any Hugging Face text-to-image model** via
`--model` (FLUX by default), runnable on a **free-tier HF token**, **stdin-pipeable** for
agent-assembled prompts, and it **registers an agent skill** so harnesses discover it. It
deliberately does *one* thing — prompt in, image file out. The optional ChatGPT provider
also accepts reference images for native image editing; it does not add a local model server.

| Tool | Model-agnostic | Free-tier path | Stdin pipe | No local server | Agent-skill registration | Single-purpose simplicity |
|---|---|---|---|---|---|---|
| **draw** | ✓ (any HF model) | ✓ (HF free token) | ✓ | ✓ | ✓ | ✓ |
| dallecli | — (OpenAI only) | — (paid key) | — | ✓ | — | ~ (also edit/filter) |
| openai-cli-art | — (OpenAI only) | — (paid key) | ~ | ✓ | — | ~ |
| Replicate CLI | ✓ (any hosted model) | ~ (free credits) | ✓ | ✓ | — | — (generic runner) |
| simonw `llm` (+ image plugins) | ✓ (via plugins) | ~ (depends on backend) | ✓ | ~ | — | — (general LLM CLI) |
| comfy-cli / ComfyUI | ✓ (local + partner) | ✓ (local) | — | — (runs a server) | — | — (full stack) |

`~` = partial. `draw` is not the most powerful — comfy-cli wins on local control and `llm`
on breadth — but it is the lightest path from a shell prompt to an image file with no vendor
lock-in and no server to babysit, which is exactly what a coding agent needs for placeholder
and concept art.

## Ecosystem

Part of the [HyperIDE.ai](https://hyperide.ai) agent toolchain:

- **[tg-cli](https://github.com/alex-mextner/tg-cli)** — simple Telegram CLI to send messages, photos & files, and a two-way agent bridge (reports, Q→buttons, voice/rich)
- **[review-cli](https://github.com/alex-mextner/review-cli)** — multi-model read-only code review from one command: diff review, cited quorum, brainstorm, visual review, and interactive spec-review tooling. Read-only, CLI-first, harness-agnostic.
- **[rig-cli](https://github.com/alex-mextner/rig-cli)** — umbrella dev-env driver: sets up a repo from config — skills, hooks, CI, dep-bootstrap; reconciles drift
- **[agent-tools](https://github.com/alex-mextner/agent-tools)** — the shared catalog `rig` applies: portable agent skills, agent-hooks, the global git-hook dispatcher, CI gates, and MCP servers
- **[3d-cli](https://github.com/alex-mextner/3d-cli)** — scriptable CLI for the full 3D FDM lifecycle: modeling, mesh repair, slicing, and print monitoring
- **[hyperide.ai](https://hyperide.ai)** — Figma replacement inside VS Code. Edit React components directly through AST/LSP without AI hallucinations, token waste, or context-window limits. Works for indie vibe-coding and for enterprise teams with split design/dev roles.

Each CLI registers a skill into your agent harnesses (`<tool> install-skill`) so agents know it exists — see Install.
