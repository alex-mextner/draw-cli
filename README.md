# draw-cli

Generate images from text prompts via the Hugging Face Inference API — designed to be called
from any shell, script, or AI coding agent without leaving the terminal.

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

**One-liner** (installs deps, links `draw` into PATH, registers the agent skill):

```bash
curl -fsSL https://raw.githubusercontent.com/alex-mextner/draw-cli/main/install.sh | bash
```

**Alternative — isolated env via pipx:**

```bash
pipx install git+https://github.com/alex-mextner/draw-cli
```

Either way, finish with token setup (see below), then optionally run the skill registration
step manually if you skipped the one-liner:

```bash
draw install-skill
```

`install-skill` is idempotent — it writes a skill file to `~/.agents/skills/draw/` so Claude
Code, Codex, opencode, and Gemini harnesses know `draw` exists. The one-liner runs it
automatically.

### Token setup

Create `~/.config/draw-cli/.env`:

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
```

## Flags

| Flag | Default | Description |
|------|---------|-------------|
| `prompt` (positional) | — | Text prompt. Reads from stdin if omitted. |
| `-o / --out` | required | Output image path (e.g. `out.png`). |
| `--model <hf-id>` | `black-forest-labs/FLUX.1-schnell` | Any HF text-to-image model ID. |

## Env vars

| Variable | Description |
|----------|-------------|
| `HF_TOKEN` | Hugging Face access token. Auto-loaded from `~/.config/draw-cli/.env`. |
| `HF_MODEL` | Default model override (same effect as `--model`). |

## Requirements

- Python 3.9+
- [`huggingface_hub`](https://pypi.org/project/huggingface_hub/) and `Pillow` Python packages

---

## Ecosystem

Part of the [HyperIDE.ai](https://hyperide.ai) agent toolchain:

- **[tg-cli](https://github.com/alex-mextner/tg-cli)** — Telegram bridge for agents: push reports, two-way control, Q→buttons
- **[review-cli](https://github.com/alex-mextner/review-cli)** — multi-model read-only code review
- **[3d-cli](https://github.com/alex-mextner/3d-cli)** — scriptable CLI for the full 3D FDM lifecycle: modeling, mesh repair, slicing, and print monitoring
- **[hyperide.ai](https://hyperide.ai)** — Figma replacement inside VS Code. Edit React components directly through AST/LSP without AI hallucinations, token waste, or context-window limits. Works for indie vibe-coding and for enterprise teams with split design/dev roles.

Each CLI registers a skill into your agent harnesses (`<tool> install-skill`) so agents know it exists — see Install.
