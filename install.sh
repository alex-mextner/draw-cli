#!/usr/bin/env bash
# install.sh — install the `draw` CLI (Python 3)
# Works both from a local clone (./install.sh) and piped from curl:
#   curl -fsSL https://raw.githubusercontent.com/alex-mextner/draw-cli/main/install.sh | bash
#
# Cleaner alternative if you use pipx:
#   pipx install git+https://github.com/alex-mextner/draw-cli
set -euo pipefail

# ── identity ──────────────────────────────────────────────────────────────────
TOOL="draw"
REPO="draw-cli"
GITHUB_USER="alex-mextner"
ENTRY="bin/draw"     # path inside repo root
CLONE_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"

# ── locate source dir ─────────────────────────────────────────────────────────
_script_dir=""
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" ]]; then
  _script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "$_script_dir" && -f "$_script_dir/$ENTRY" ]]; then
  SRC="$_script_dir"
  echo "draw: using local clone at $SRC"
else
  mkdir -p "$CLONE_BASE"
  CLONE_DIR="$CLONE_BASE/$REPO"
  EXPECT_URL="https://github.com/$GITHUB_USER/$REPO.git"
  if [[ -d "$CLONE_DIR/.git" ]]; then
    actual_url="$(git -C "$CLONE_DIR" remote get-url origin 2>/dev/null || echo "")"
    if [[ "$actual_url" != "$EXPECT_URL" ]]; then
      echo "ERROR: $CLONE_DIR exists but its origin is '$actual_url', not $EXPECT_URL." >&2
      echo "       Remove that directory or fix its remote, then re-run." >&2
      exit 1
    fi
    echo "draw: updating existing clone at $CLONE_DIR"
    git -C "$CLONE_DIR" pull --ff-only
  else
    echo "draw: cloning $EXPECT_URL into $CLONE_DIR"
    git clone "$EXPECT_URL" "$CLONE_DIR"
  fi
  SRC="$CLONE_DIR"
fi

# ── bin dir ───────────────────────────────────────────────────────────────────
BIN="$HOME/.local/bin"
mkdir -p "$BIN"

if [[ ":$PATH:" != *":$BIN:"* ]]; then
  echo ""
  echo "  NOTE: $BIN is not on your PATH."
  echo "  Add the following line to your ~/.bashrc or ~/.zshrc and restart your shell:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo ""
fi

# ── dependency: huggingface_hub (required) ────────────────────────────────────
# Cleaner option: pipx install git+https://github.com/alex-mextner/draw-cli
# (pipx creates an isolated venv and puts `draw` on PATH automatically)
# Use the SAME python3 for the import check and the install, and pull every
# runtime dep (huggingface_hub + Pillow, which draw needs to save images).
if ! python3 -c 'import huggingface_hub, PIL' 2>/dev/null; then
  echo "draw: installing runtime deps via: python3 -m pip install --user huggingface_hub Pillow"
  if ! python3 -m pip install --user huggingface_hub Pillow; then
    echo ""
    echo "  ERROR: could not install huggingface_hub / Pillow. draw requires them."
    echo "  Install manually: python3 -m pip install --user huggingface_hub Pillow"
    echo "  Or use pipx:      pipx install git+https://github.com/$GITHUB_USER/$REPO"
    echo ""
    exit 1
  fi
fi

# ── symlink entry ─────────────────────────────────────────────────────────────
ENTRY_PATH="$SRC/$ENTRY"
chmod +x "$ENTRY_PATH"
ln -sfn "$ENTRY_PATH" "$BIN/$TOOL"
echo "draw: symlinked $BIN/$TOOL -> $ENTRY_PATH"

# ── register skill ────────────────────────────────────────────────────────────
if ! "$BIN/$TOOL" install-skill; then
  echo "  WARNING: '$TOOL install-skill' failed — $TOOL is installed but agents may not"
  echo "           auto-discover it. Re-run '$TOOL install-skill' manually to fix."
fi

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
echo "  draw is installed."
echo "  Usage: draw \"a cute robot\" -o robot.png   — generate image from prompt"
echo "         draw --model <hf-model-id> ...    — use a specific HF model"
echo "         draw --help                       — full usage"
echo "  Auth:  set HF_TOKEN env var or put it in ~/.config/draw-cli/.env"
echo ""
