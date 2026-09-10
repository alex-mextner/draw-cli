#!/usr/bin/env bash
# install.sh — install the `draw` CLI (Python 3)
# Works both from a local clone (./install.sh) and piped from curl:
#   curl -fsSL https://raw.githubusercontent.com/alex-mextner/draw-cli/main/install.sh | bash
#
# draw's Python runtime deps (huggingface_hub + Pillow) are installed here.
# The optional ChatGPT backend additionally uses the separately installed Codex CLI.
set -euo pipefail

TOOL="draw"
REPO="draw-cli"
GITHUB_USER="alex-mextner"
ENTRY="bin/draw"
CLONE_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"

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

BIN="${PIPX_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN"

if [[ ":$PATH:" != *":$BIN:"* ]]; then
  echo ""
  echo "  NOTE: $BIN is not on your PATH."
  echo "  Add the following line to your ~/.bashrc or ~/.zshrc and restart your shell:"
  echo "    export PATH=\"$BIN:\$PATH\""
  echo ""
fi

DRAW_BIN=""
INSTALL_MODE=""
if command -v pipx >/dev/null 2>&1; then
  INSTALL_MODE="pipx"
  echo "draw: installing via pipx (isolated venv with huggingface_hub + Pillow)"
  pipx install --force "$SRC"
  if [[ -x "$BIN/$TOOL" ]]; then
    DRAW_BIN="$BIN/$TOOL"
  else
    DRAW_BIN="$(command -v "$TOOL" 2>/dev/null || true)"
  fi
  if [[ -z "$DRAW_BIN" || ! -x "$DRAW_BIN" ]]; then
    echo "  ERROR: pipx install succeeded but '$TOOL' is not on PATH (check $BIN / PIPX_BIN_DIR)" >&2
    exit 1
  fi
  echo "draw: pipx installed $TOOL at $DRAW_BIN"
else
  INSTALL_MODE="symlink"
  echo ""
  echo "  NOTE: pipx not found — falling back to a symlink + 'pip install --user'."
  echo "  For a clean ISOLATED install (recommended), install pipx and re-run:"
  echo "    python3 -m pip install --user pipx && python3 -m pipx ensurepath"
  echo ""
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
  ENTRY_PATH="$SRC/$ENTRY"
  chmod +x "$ENTRY_PATH"
  ln -sfn "$ENTRY_PATH" "$BIN/$TOOL"
  DRAW_BIN="$BIN/$TOOL"
  echo "draw: symlinked $BIN/$TOOL -> $ENTRY_PATH"
fi

RESOLVED="$(command -v "$TOOL" 2>/dev/null || true)"
if [[ -n "$RESOLVED" && "$RESOLVED" != "$DRAW_BIN" ]]; then
  echo ""
  echo "  WARNING: another '$TOOL' shadows our install on PATH:" >&2
  echo "      installed: $DRAW_BIN" >&2
  echo "      resolves to: $RESOLVED" >&2
  echo "  Ensure the dir holding our install precedes the other on PATH, or remove the other." >&2
  echo ""
fi

if ! "$DRAW_BIN" install-skill; then
  echo "  WARNING: '$TOOL install-skill' failed — $TOOL is installed but agents may not"
  echo "           auto-discover it. Re-run '$TOOL install-skill' manually to fix."
fi

PATH_RESOLVED="$(command -v "$TOOL" 2>/dev/null || true)"
if [[ -z "$PATH_RESOLVED" ]]; then
  echo "" >&2
  echo "  WARNING: $TOOL is installed at $DRAW_BIN, but does NOT resolve by name on PATH." >&2
  echo "           ($BIN is not on your PATH, so the bare '$TOOL' command will not work yet.)" >&2
  echo "  Fix it:  add $BIN to PATH, then restart your shell:" >&2
  echo "             export PATH=\"$BIN:\$PATH\"" >&2
  echo "  Until then, run $TOOL by full path: $DRAW_BIN" >&2
  echo "" >&2
  exit 1
fi

echo ""
echo "  draw is installed (via $INSTALL_MODE)."
echo ""
echo "  Hugging Face (default):"
echo "    draw \"a cute robot\" -o robot.png"
echo "    Auth: set HF_TOKEN or put it in ~/.config/draw-cli/.env"
echo ""
echo "  ChatGPT plan via Codex (no OpenAI API key):"
echo "    npm install -g @openai/codex@latest"
echo "    codex login                 # sign in with ChatGPT"
echo "    draw --backend chatgpt --check"
echo "    draw \"a cute robot\" --backend chatgpt -o robot.png"
echo ""
echo "  Make ChatGPT the default with DRAW_BACKEND=chatgpt."
echo "  Full usage: draw --help"
echo "  Docs: https://github.com/$GITHUB_USER/$REPO#readme"
echo ""
