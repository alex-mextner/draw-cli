# draw-cli

Generate images from text prompts via Hugging Face Inference Providers, the Stability AI API,
or local Stable Diffusion 3.5 Large — designed to be called from any shell, script, or AI coding
agent without leaving the terminal.

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

draw needs `huggingface_hub>=0.34,<2` + `Pillow` at runtime, so the recommended path is **pipx** —
an isolated venv with the deps and `draw` on your PATH. API use does not install PyTorch:

```bash
pipx install git+https://github.com/alex-mextner/draw-cli
```

**One-liner** (pipx-first: uses pipx if present, else symlinks `draw` into PATH and installs
deps with `pip --user`; either way registers the agent skill):

```bash
curl -fsSL https://raw.githubusercontent.com/alex-mextner/draw-cli/main/install.sh | bash
```

For an existing legacy symlink install, upgrade its runtime dependencies too:

```bash
python3 -m pip install --user --upgrade 'huggingface_hub>=0.34,<2' Pillow
```

Either way, finish with token setup (see below), then run the skill registration step
manually if you installed with raw pipx (the one-liner runs it for you):

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
# Optional: required only for --backend stability
# STABILITY_API_KEY=your_key
```

Get a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
with Inference Providers permission for hosted inference. Local gated downloads need access
to the model repository after accepting its conditions. `hf auth login` is also supported.
Hosted availability, quotas and charges depend on the provider/account; a token does not
make every model available or free.

## Usage

```bash
# Prompt as positional arg; HF/FLUX remains the default
draw "a cute robot" -o robot.png

# Override model
draw "a cute robot" --model black-forest-labs/FLUX.1-dev -o robot.png

# Stable Diffusion 3.5 Large through Hugging Face
draw "a cute robot" --model sd3.5 -o robot.png

# Stable Diffusion 3.5 Large through Stability AI directly
draw "a cute robot" --backend stability --aspect-ratio 16:9 -o robot.png

# Prompt from stdin
echo "a cute robot" | draw -o robot.png

# Print version and exit (no -o needed)
draw --version
```

### Local Stable Diffusion 3.5 Large

Install the optional local dependencies, accept the model's Hugging Face access conditions,
and check resources before downloading weights:

```bash
# In a checkout/virtual environment:
python -m pip install ".[local]"

draw --check-resources
draw --check-resources --cache-dir /mnt/models/huggingface --json
draw "a cute robot" --backend local --seed 42 -o robot.png
```

Local generation always checks cache/output disk space, available RAM, cgroup limits and
free GPU memory before loading. It supports CUDA, Apple MPS and explicitly requested CPU,
automatic dtype/CPU offload, pinned model revisions and offline cached execution.
It never switches to a paid API when resources are insufficient. RAM/VRAM thresholds are
conservative estimates, not guarantees against OOM.

See [the SD 3.5 guide](docs/stable-diffusion-3.5.md) for pipx extras, hardware estimates,
cache accounting, offline operation and backend-specific options.

## Flags

| Flag | Default | Description |
|------|---------|-------------|
| `prompt` (positional) | — | Text prompt. Reads from stdin if omitted. |
| `-o / --out` | required for generation | Output image path; parent directory must exist. |
| `--backend` | `hf` | `hf`/`api`, `stability`, or `local`. |
| `--model` | FLUX for HF; SD 3.5 Large otherwise | HF model ID or `sd3.5` alias. |
| `--provider` | `auto` | Hugging Face Inference Provider. HF only. |
| `--negative-prompt`, `--seed` | backend default | Negative prompt; seed from 0 through 4294967295. |
| `--width`, `--height` | local: 1024 | HF/local dimensions; local must be multiples of 16. |
| `--steps`, `--guidance-scale` | local: 28 / 3.5 | HF/local generation controls. |
| `--aspect-ratio` | `1:1` | Direct Stability API only. |
| `--timeout` | 300 seconds | API timeout. |
| `--device` | `auto` | Local: `cuda`, `cuda:N`, `mps`, `cpu`, `auto`. |
| `--dtype` | `auto` | Local: `float16`, `bfloat16`, `float32`, `auto`. |
| `--offload` | `auto` | Local: `none`, `model`, `sequential`, `auto`. |
| `--cache-dir`, `--revision` | HF cache / `main` | Local cache location and model revision. |
| `--offline` | off | Local: use a snapshot previously downloaded through draw. |
| `--check-resources` | — | Local diagnostics without weights download; no prompt/output required. |
| `--json` | off | Machine-readable resource report; exit 0 = pass, 1 = blocked. |
| `-V / --version` | — | Print the version and exit. Works without `-o`. |

Unsupported combinations fail explicitly rather than ignoring flags. Use `draw --help` for
argument details. Plain `--check-resources` selects local unless `--backend` or `DRAW_BACKEND`
explicitly selects another backend.

## Env vars

| Variable | Description |
|----------|-------------|
| `HF_TOKEN` | Hugging Face access token; alternatively use a cached login. |
| `STABILITY_API_KEY` | Direct Stability AI API key; not an HF token. |
| `HF_MODEL` | Default HF backend model override. |
| `DRAW_BACKEND` | Default backend override; an explicit flag wins. |
| `HF_HOME`, `HF_HUB_CACHE` | Hugging Face cache roots, honored by local mode. |
| `HF_HUB_OFFLINE` | Set to `1` for local offline mode. |

Variables are auto-loaded from `~/.config/draw-cli/.env` without replacing existing environment values.

## Requirements

- Python 3.9+ for the base CLI; Python 3.10+ recommended for the optional local stack.
- [`huggingface_hub`](https://pypi.org/project/huggingface_hub/) (>=0.34,<2) and `Pillow`.
- Optional `[local]` dependencies and sufficient resources for local SD 3.5 Large.

---

## How draw compares

The other text-to-image CLIs trade off between *simple-but-locked-in* and
*powerful-but-heavy*. Single-vendor tools (dallecli, openai-cli-art) are hard-wired
to one provider. General runners (Replicate CLI, simonw `llm`) cover broader AI
workflows. Local engines such as ComfyUI provide substantially more workflow control.

`draw` keeps **one command**, **stdin-pipeable prompts**, **plain image output**,
**no local server**, and **agent-skill registration**. Its base API installation
stays lightweight; SD 3.5 local inference is an explicit optional extra, not a
large download imposed on every user. It deliberately does one thing — prompt
in, image file out — and leaves editing/filtering to image tools.

## Ecosystem

Part of the [HyperIDE.ai](https://hyperide.ai) agent toolchain:

- **[tg-cli](https://github.com/alex-mextner/tg-cli)** — simple Telegram CLI to send messages, photos & files, and a two-way agent bridge (reports, Q→buttons, voice/rich)
- **[review-cli](https://github.com/alex-mextner/review-cli)** — multi-model read-only code review from one command: diff review, cited quorum, brainstorm, visual review, and interactive spec-review tooling. Read-only, CLI-first, harness-agnostic.
- **[rig-cli](https://github.com/alex-mextner/rig-cli)** — umbrella dev-env driver: sets up a repo from config — skills, hooks, CI, dep-bootstrap; reconciles drift
- **[agent-tools](https://github.com/alex-mextner/agent-tools)** — the shared catalog `rig` applies: portable agent skills, agent-hooks, the global git-hook dispatcher, CI gates, and MCP servers
- **[3d-cli](https://github.com/alex-mextner/3d-cli)** — scriptable CLI for the full 3D FDM lifecycle: modeling, mesh repair, slicing, and print monitoring
- **[hyperide.ai](https://hyperide.ai)** — Figma replacement inside VS Code. Edit React components directly through AST/LSP without AI hallucinations, token waste, or context-window limits. Works for indie vibe-coding and for enterprise teams with split design/dev roles.

Each CLI registers a skill into your agent harnesses (`<tool> install-skill`) so agents know it exists — see Install.
