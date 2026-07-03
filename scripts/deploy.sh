#!/usr/bin/env bash
# scripts/deploy.sh — update an installed `draw` checkout to the latest committed code.
#
# Invoked by `rig apply` (rig-cli 0.8.0+) on every apply to keep the tool fresh; also
# safe to run by hand. Two install modes exist and BOTH are handled:
#   * pipx (the primary path): `pipx install <checkout>` snapshots the package into an
#     isolated venv, so a plain `git pull` does NOT refresh the running `draw` — after a
#     fast-forward this script re-runs `pipx install --force <checkout>`.
#   * legacy symlink (install.sh fallback): bin/draw is a live shim into the checkout,
#     so the pull alone IS the deploy; the pipx step is skipped when pipx has no
#     draw-cli venv.
#
# Git safety mirrors tg-cli's scripts/deploy.sh: refuse (rc 1) on a dirty worktree,
# detached HEAD, or missing origin/upstream; rc 2 on a non-fast-forward (diverged)
# branch; rc 0 when already up to date (pipx reinstall skipped unless the installed
# version drifted), when ahead of origin (nothing to pull), and when deployed.
# Pulls come from `origin` only — never a fork some branch happens to track.
# After a deploy the agent skill is refreshed via the checkout's own
# `bin/draw install-skill` (same as install.sh does).
set -euo pipefail

usage() {
  cat <<'EOF'
deploy.sh — update an installed `draw` checkout to the latest committed code.

Fast-forwards the checkout from origin (only origin — never another remote),
then refreshes the pipx-installed `draw` (pipx snapshots the package at install
time, so a git pull alone never reaches the running binary) and re-registers
the agent skill (`bin/draw install-skill`). Legacy symlink installs need only
the pull + skill refresh.

Usage:
  scripts/deploy.sh [--checkout DIR] [--dry-run]

  --checkout DIR   The git checkout to update. Default: the repo containing
                   this script.
  --dry-run        Fetch and report what would land without pulling or
                   touching pipx. Always safe to run.

Environment:
  DRAW_DEPLOY_SKIP_PIPX=1        Skip the pipx refresh step (tests / symlink-only).
  DRAW_DEPLOY_EXPECTED_ORIGIN=U  Accept origin URL U instead of the official
                                 github.com/alex-mextner/draw-cli repo (tests /
                                 a deliberate mirror).

Exit codes: 0 up-to-date/ahead/deployed · 1 usage/env/dirty/detached error · 2 non-fast-forward.
EOF
}

CHECKOUT=""
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --checkout)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "deploy: --checkout requires a directory argument." >&2; exit 1
      fi
      CHECKOUT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "deploy: unknown argument '$1' (try --help)" >&2; exit 1 ;;
  esac
done

# Scrub repo-pinning GIT_* vars from every git invocation: when this script runs
# from inside a git hook (rig apply triggered by a hook, a hook-spawned shell),
# the environment carries GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE which OVERRIDE
# `git -C` and would silently pin every command to a FOREIGN repo (the exact bug
# class fixed in review-cli#72). `env -u` of an absent var is a no-op, so this
# is safe everywhere. Kept as an array so the fetch below can wrap the whole
# chain in `timeout` (timeout execs a real command, not a shell function).
GIT_ENV_SCRUB=(-u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY
               -u GIT_COMMON_DIR -u GIT_CEILING_DIRECTORIES -u GIT_DISCOVERY_ACROSS_FILESYSTEM)
git_clean() { env "${GIT_ENV_SCRUB[@]}" git "$@"; }

# Resolve the real file behind a symlink hop-by-hop (`readlink -f` is absent on
# stock macOS). A depth cap breaks a symlink cycle instead of looping forever.
resolve_link() {
  target="$1"
  hops=0
  while [ -L "$target" ]; do
    hops=$((hops + 1))
    if [ "$hops" -gt 40 ]; then
      echo "deploy: symlink chain for '$1' is too deep (cycle?) — aborting." >&2
      exit 1
    fi
    link="$(readlink "$target")"
    case "$link" in
      /*) target="$link" ;;                      # absolute
      *)  target="$(dirname "$target")/$link" ;; # relative to its own dir
    esac
  done
  echo "$target"
}

# ── resolve which checkout to deploy ─────────────────────────────────────────
# Default order (no --checkout):
#   1. The repo THIS script lives in: rig's contract is `bash <repo>/scripts/
#      deploy.sh` with no args — "keep the repo you live in fresh". A PATH-first
#      resolution could deploy the WRONG repo (the pipx venv shim on PATH never
#      points back at a checkout at all).
#   2. Fall back to resolving a legacy-symlink `draw` on PATH through its
#      symlink chain — for a stray copy of this script outside any checkout —
#      but only when the resolved shim is the candidate repo's own bin/draw.
if [ -z "$CHECKOUT" ]; then
  script_target="$(resolve_link "${BASH_SOURCE[0]}")" || exit 1
  # `cd` output to /dev/null: with CDPATH set, bash prints the resolved dir.
  script_dir="$(cd "$(dirname "$script_target")" >/dev/null && pwd -P)"
  CHECKOUT="$(git_clean -C "$script_dir" rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [ -z "$CHECKOUT" ]; then
  draw_bin="$(command -v draw || true)"
  if [ -n "$draw_bin" ]; then
    draw_real="$(resolve_link "$draw_bin")" || exit 1
    draw_dir="$(cd "$(dirname "$draw_real")" >/dev/null && pwd -P)"
    candidate="$(git_clean -C "$draw_dir" rev-parse --show-toplevel 2>/dev/null || true)"
    # Trust the PATH fallback only when `draw` resolves to the candidate's OWN
    # bin/draw shim and the package exists — a pipx venv shim (or any foreign
    # repo's script) fails this identity check, so we never fetch/merge a
    # repository the installed binary does not actually point into.
    if [ -n "$candidate" ] \
       && [ "$draw_dir/$(basename "$draw_real")" = "$candidate/bin/draw" ] \
       && [ -f "$candidate/draw_cli/__init__.py" ]; then
      CHECKOUT="$candidate"
    fi
  fi
  if [ -z "$CHECKOUT" ]; then
    echo "deploy: could not locate a checkout (script outside any git repo, and" >&2
    echo "        no symlink-installed 'draw' on PATH resolving into one)." >&2
    echo "        Pass --checkout DIR to name the checkout to update." >&2
    exit 1
  fi
fi

git_c() { git_clean -C "$CHECKOUT" "$@"; }

checkout_version() {
  # The single source of truth for the package version (pyproject reads it
  # dynamically). awk reads the FILE directly — no pipe, so its early exit
  # cannot SIGPIPE an upstream producer under `set -o pipefail`.
  awk -F'"' '/^__version__ = /{print $2; exit}' "$CHECKOUT/draw_cli/__init__.py"
}

pipx_installed_version() {
  # "draw-cli 0.2.1" in `pipx list --short` → "0.2.1"; empty stdout when not
  # pipx-installed (or pipx itself is absent). rc 1 when pipx IS present but
  # `pipx list` errors — "pipx failed" must be distinguishable from "not
  # installed", or a broken pipx would silently skip the reinstall and leave the
  # running `draw` stale. awk consumes ALL input (match saved, printed in END) —
  # an early `exit` would SIGPIPE pipx and, under pipefail, fake a failure.
  command -v pipx >/dev/null 2>&1 || return 0
  pipx_out="$(pipx list --short 2>/dev/null)" || return 1
  printf '%s\n' "$pipx_out" | awk '$1 == "draw-cli" { v = $2 } END { if (v) print v }'
}

refresh_skill() {
  # install.sh registers the agent skill via `draw install-skill`; a deploy that
  # changed the skill content must refresh it too. Run the deployed checkout's OWN
  # shim (works for both install modes; absent in throwaway test repos → no-op).
  shim="$CHECKOUT/bin/draw"
  [ -x "$shim" ] || return 0
  if bounded 30 "$shim" install-skill >/dev/null 2>&1; then
    echo "deploy: refreshed draw skill (install-skill)"
  else
    echo "deploy: WARNING — 'draw install-skill' failed/timed out; re-run it manually." >&2
  fi
}

timeout_bin="$(command -v timeout || command -v gtimeout || true)"
[ -z "$timeout_bin" ] && echo "deploy: NOTE — no timeout(1)/gtimeout; long-running steps run unbounded." >&2
bounded() {
  # Cap a child (SECONDS first arg) so a hung invocation can't wedge rig apply.
  secs="$1"; shift
  if [ -n "$timeout_bin" ]; then "$timeout_bin" "$secs" "$@"; else "$@"; fi
}

refresh_pipx() {
  # Re-snapshot the checkout into the pipx venv. Skipped (with a note) when pipx is
  # absent or draw-cli is not a pipx install (legacy symlink mode: the pull sufficed).
  if [ "${DRAW_DEPLOY_SKIP_PIPX:-0}" = "1" ]; then
    echo "deploy: pipx refresh skipped (DRAW_DEPLOY_SKIP_PIPX=1)."
    return 0
  fi
  if ! command -v pipx >/dev/null 2>&1; then
    echo "deploy: pipx not on PATH — assuming a symlink install; the pull is the deploy."
    return 0
  fi
  # A pipx-list FAILURE is fatal here (post-pull): treating it as "not installed"
  # would skip the reinstall and exit 0 while the running `draw` stays stale.
  if ! installed_version="$(pipx_installed_version)"; then
    echo "deploy: 'pipx list' failed — cannot tell whether draw-cli is pipx-installed." >&2
    echo "        Fix pipx, then re-run: pipx install --force $CHECKOUT" >&2
    exit 1
  fi
  if [ -z "$installed_version" ]; then
    echo "deploy: draw-cli is not pipx-installed — symlink install; the pull is the deploy."
    return 0
  fi
  echo "deploy: refreshing pipx install from $CHECKOUT ..."
  if ! bounded 600 pipx install --force "$CHECKOUT" >/dev/null; then
    echo "deploy: pipx install --force failed — the installed draw is still the OLD version." >&2
    echo "        Re-run: pipx install --force $CHECKOUT" >&2
    exit 1
  fi
  echo "deploy: pipx now at draw-cli $(pipx_installed_version)"
}

verify_checkout() {
  if ! git_c rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "deploy: '$CHECKOUT' is not a git checkout." >&2
    echo "        Re-run install.sh to refresh a non-clone install." >&2
    exit 1
  fi

  dirty="$(git_c status --porcelain --untracked-files=no)"
  if [ -n "$dirty" ]; then
    echo "deploy: checkout has local (tracked) changes — refusing to pull over them." >&2
    echo "        Commit, stash, or discard them, then re-run." >&2
    echo "$dirty" >&2
    exit 1
  fi

  branch="$(git_c rev-parse --abbrev-ref HEAD)"
  if [ "$branch" = "HEAD" ]; then
    echo "deploy: checkout is in detached-HEAD state — no branch to pull." >&2
    echo "        Check out a branch (e.g. 'git -C $CHECKOUT switch main') first." >&2
    exit 1
  fi
  echo "deploy: branch  = $branch"
}

echo "deploy: checkout = $CHECKOUT"
verify_checkout
# Normalize an explicit `--checkout <repo>/subdir` to the repo root, so the
# version probe and skill refresh look at the right paths.
CHECKOUT="$(git_c rev-parse --show-toplevel)"

# Deploys are PINNED to origin (never the branch's @{u}): install.sh enforces the
# official repo as origin, and rig apply must never auto-install code fetched from
# whatever other remote a branch happens to track.
remote="origin"
if ! origin_url="$(git_c remote get-url "$remote" 2>/dev/null)"; then
  echo "deploy: no remote '$remote' configured — nothing to pull from." >&2
  exit 1
fi

# Validate WHERE origin actually points, not just that a remote named "origin"
# exists: this script fetches, merges, and `pipx install --force`s that code
# unattended, so a checkout whose origin was re-pointed at a fork must refuse
# (install.sh applies the same check). Normalize the ssh/https spellings of the
# official repo; DRAW_DEPLOY_EXPECTED_ORIGIN overrides for tests / a deliberate
# mirror.
normalize_url() {
  u="${1%/}"
  u="${u%.git}"
  case "$u" in
    git@github.com:*)       u="https://github.com/${u#git@github.com:}" ;;
    ssh://git@github.com/*) u="https://github.com/${u#ssh://git@github.com/}" ;;
  esac
  printf '%s\n' "$u"
}
expected_origin="${DRAW_DEPLOY_EXPECTED_ORIGIN:-https://github.com/alex-mextner/draw-cli}"
if [ "$(normalize_url "$origin_url")" != "$(normalize_url "$expected_origin")" ]; then
  echo "deploy: origin points at '$origin_url', not the official repo" >&2
  echo "        ($expected_origin) — refusing to auto-install code from it." >&2
  echo "        Fix the remote (git -C $CHECKOUT remote set-url origin ...) or set" >&2
  echo "        DRAW_DEPLOY_EXPECTED_ORIGIN for a deliberate mirror." >&2
  exit 1
fi
# A failed fetch (network/auth down) must be the documented friendly exit 1, not
# a raw set -e abort. It is also the step most likely to HANG, and rig apply
# cannot downgrade a hung deploy — bound it, and fail fast instead of wedging an
# unattended run on an HTTPS auth / credential-manager prompt.
if ! bounded 120 env "${GIT_ENV_SCRUB[@]}" GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never \
     git -C "$CHECKOUT" fetch "$remote" --quiet; then
  echo "deploy: 'git fetch $remote' failed or timed out — check the remote/network/auth and re-run." >&2
  exit 1
fi
# Fully-qualified upstream ref for all plumbing: a short "origin/main" can resolve
# an ambiguous local ref or TAG named "origin/main" instead of the remote-tracking
# branch. $upstream_name is only for human-readable messages.
upstream_name="${remote}/${branch}"
upstream="refs/remotes/${remote}/${branch}"
if ! git_c rev-parse --verify --quiet "$upstream" >/dev/null; then
  echo "deploy: no upstream '$upstream_name' — is this branch pushed?" >&2
  exit 1
fi

resync_pipx_on_version_drift() {
  # An up-to-date checkout can still outrun the pipx venv — e.g. someone pulled
  # by hand without reinstalling. Re-sync on version drift; otherwise no-op.
  # Known limit (up-to-date path only): code changed WITHOUT bumping
  # __version__ is invisible here — acceptable because every release bumps the
  # version (bump-version-on-release is a ship-gate rule for this repo), and the
  # normal rig path reinstalls on pull.
  if [ "$DRY_RUN" = "0" ] && [ "${DRAW_DEPLOY_SKIP_PIPX:-0}" != "1" ]; then
    if ! installed="$(pipx_installed_version)"; then
      echo "deploy: 'pipx list' failed — cannot tell whether the installed draw is stale." >&2
      echo "        Fix pipx, then re-run this deploy." >&2
      exit 1
    fi
    wanted="$(checkout_version || true)"
    if [ -n "$installed" ] && [ -n "$wanted" ] && [ "$installed" != "$wanted" ]; then
      echo "deploy: pipx has draw-cli $installed but the checkout is $wanted — re-syncing."
      refresh_pipx
      refresh_skill
    fi
  fi
}

local_sha="$(git_c rev-parse HEAD)"
remote_sha="$(git_c rev-parse "$upstream")"

if [ "$local_sha" = "$remote_sha" ]; then
  echo "deploy: already up to date ($(git_c rev-parse --short HEAD))."
  resync_pipx_on_version_drift
  exit 0
fi

# Ahead (local commits not yet on origin) is NOT divergence: there is nothing to
# pull — but the pipx venv cannot be proven current from the version alone (a
# committed local change WITHOUT a bump is invisible to a drift check), so when
# draw-cli is pipx-installed, reinstall unconditionally instead of exiting 0 over
# a possibly-stale venv. refresh_pipx itself no-ops for symlink installs.
if git_c merge-base --is-ancestor "$upstream" HEAD; then
  echo "deploy: checkout is ahead of '$upstream_name' (unpushed commits) — nothing to pull."
  if [ "$DRY_RUN" = "0" ]; then
    refresh_pipx
    refresh_skill
  fi
  exit 0
fi

if ! git_c merge-base --is-ancestor HEAD "$upstream"; then
  echo "deploy: cannot fast-forward — '$branch' has diverged from '$upstream_name'." >&2
  echo "        A human must reconcile (rebase/merge). Aborting." >&2
  exit 2
fi

echo "deploy: $(git_c rev-parse --short HEAD) -> $(git_c rev-parse --short "$upstream"), commits to land:"
git_c log --oneline "HEAD..$upstream" | sed 's/^/  /'

if [ "$DRY_RUN" = "1" ]; then
  echo "deploy: --dry-run — not pulling."
  exit 0
fi

# `merge --ff-only` (not `pull`): we already fetched and validated $upstream is a
# strict descendant of HEAD, so this updates against the same object state the
# divergence check saw. The one way it still fails after the clean/ancestor
# checks: an UNTRACKED local file colliding with a tracked file the upstream adds
# (untracked files deliberately don't block above, but git refuses to overwrite
# one). Surface that as the documented friendly exit 1, not a raw set -e abort.
# `--no-overwrite-ignore` extends the refusal to IGNORED local files (a stray
# cache upstream starts tracking) instead of silently clobbering them.
if ! git_c merge --ff-only --no-overwrite-ignore --quiet "$upstream"; then
  echo "deploy: fast-forward failed — most likely an untracked/ignored local file" >&2
  echo "        collides with a file this deploy adds (git refuses to overwrite it)." >&2
  echo "        Move/remove the file named in the git error above, then re-run." >&2
  exit 1
fi
new_sha="$(git_c rev-parse --short HEAD)"
echo "deploy: pulled — now at $new_sha"

refresh_pipx
refresh_skill

echo "deploy: done — deployed $new_sha."
