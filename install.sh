#!/usr/bin/env bash
# install.sh — install the `draw` CLI (Python 3)
# Works both from a local clone (./install.sh) and piped from curl:
#   curl -fsSL https://raw.githubusercontent.com/alex-mextner/draw-cli/main/install.sh | bash
#
# draw's runtime deps (huggingface_hub + Pillow) are REQUIRED, so this installer is
# PIPX-FIRST: when pipx is present it gets an isolated venv with the deps + `draw` on
# PATH. Without pipx it falls back to a symlink + `pip install --user`.
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
BIN="${PIPX_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN"

if [[ ":$PATH:" != *":$BIN:"* ]]; then
  echo ""
  echo "  NOTE: $BIN is not on your PATH."
  echo "  Add the following line to your ~/.bashrc or ~/.zshrc and restart your shell:"
  echo "    export PATH=\"$BIN:\$PATH\""
  echo ""
fi

# ── install ───────────────────────────────────────────────────────────────────
# PIPX-FIRST: an isolated venv carries huggingface_hub + Pillow with zero pollution of
# the system/user site-packages, and `pipx install --force` makes re-runs idempotent.
DRAW_BIN=""
INSTALL_MODE=""
if command -v pipx >/dev/null 2>&1; then
  INSTALL_MODE="pipx"
  echo "draw: installing via pipx (isolated venv with huggingface_hub + Pillow)"
  pipx install --force "$SRC"
  # Resolve the entry point pipx just dropped. Prefer the expected $BIN/$TOOL; if pipx
  # landed it elsewhere, fall back to whatever is now on PATH. If NEITHER resolves to an
  # executable, fail fast — never continue with an empty or foreign binary below.
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
  # Runtime deps are REQUIRED: use the SAME python3 for the import check and the install,
  # and pull every dep (huggingface_hub + Pillow, needed to save images).
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

# ── shadow check ──────────────────────────────────────────────────────────────
# We installed `draw` at $DRAW_BIN, but a different `draw` EARLIER on PATH silently wins.
# Just WARN (don't touch it — it may be intentional); the user resolves the PATH order.
RESOLVED="$(command -v "$TOOL" 2>/dev/null || true)"
if [[ -n "$RESOLVED" && "$RESOLVED" != "$DRAW_BIN" ]]; then
  echo ""
  echo "  WARNING: another '$TOOL' shadows our install on PATH:" >&2
  echo "      installed: $DRAW_BIN" >&2
  echo "      resolves to: $RESOLVED" >&2
  echo "  Ensure the dir holding our install precedes the other on PATH, or remove the other." >&2
  echo ""
fi

# ── register skill ────────────────────────────────────────────────────────────
# Invoke OUR install (absolute path), never the bare name — a PATH shadow would otherwise
# run a different binary.
if ! "$DRAW_BIN" install-skill; then
  echo "  WARNING: '$TOOL install-skill' failed — $TOOL is installed but agents may not"
  echo "           auto-discover it. Re-run '$TOOL install-skill' manually to fix."
fi

# ── done ──────────────────────────────────────────────────────────────────────
# Gate the success report on PATH resolution: pipx/symlink drops $DRAW_BIN, but if $BIN is
# not on PATH then `draw` by NAME does not work. Don't report a clean "installed" in that
# case — warn loudly so the user fixes PATH instead of believing the bare command works.
# (The shadow check above only fires when a DIFFERENT `draw` resolves; an unreachable $BIN
# leaves RESOLVED empty, so it stays silent — this closes that gap.)
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
echo "  Usage: draw \"a cute robot\" -o robot.png   — generate image from prompt"
echo "         draw --model <hf-model-id> ...    — use a specific HF model"
echo "         draw --help                       — full usage"
echo "  Auth:  set HF_TOKEN env var or put it in ~/.config/draw-cli/.env"
echo "  Tip:   pipx is the primary path — 'pipx install git+https://github.com/$GITHUB_USER/$REPO'"
echo ""
