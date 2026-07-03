"""test_deploy.py — behavior tests for scripts/deploy.sh (the rig-apply freshness hook).

Pins the git-safety contract the script promises:
  rc 0  already up to date / ahead of origin / deployed (fast-forward pulled)
  rc 1  dirty worktree / detached HEAD / missing origin / usage / failed reinstall
  rc 2  non-fast-forward (local branch diverged from origin)

Each test builds a throwaway origin repo + clone under tmp_path and runs the REAL
script against the clone via --checkout. DRAW_DEPLOY_SKIP_PIPX=1 keeps every run
away from the developer's actual pipx venv — these tests must never reinstall the
live tool. Stdlib-only (subprocess + pytest's tmp_path); needs git + bash, which
both the dev machine and the CI runner have.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SH = _REPO_ROOT / "scripts" / "deploy.sh"


def _git_env() -> dict:
    """An env that isolates test repos from the developer's global git config —
    especially a global core.hooksPath (commit hooks like a review gate would
    otherwise fire inside the throwaway repos and fail the commits)."""
    return dict(
        os.environ,
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    ).stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(
        repo,
        "-c", "user.name=test",
        "-c", "user.email=test@example.com",
        "commit", "-m", message,
    )


def _make_origin_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """A non-bare origin with one commit, plus a clone of it (the 'installed checkout')."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _commit(origin, "hello.txt", "v1\n", "initial commit")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    return origin, clone


def _origin_url_of(checkout: Path) -> str:
    """The checkout's actual origin URL (a tmp_path in these tests), used as
    DRAW_DEPLOY_EXPECTED_ORIGIN so the official-repo URL gate — tested on its
    own in test_foreign_origin_url_refuses — stays out of unrelated tests."""
    try:
        return _git(checkout, "remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return ""


def _run_deploy(checkout: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(
        _git_env(),
        DRAW_DEPLOY_SKIP_PIPX="1",
        DRAW_DEPLOY_EXPECTED_ORIGIN=_origin_url_of(checkout),
    )
    return subprocess.run(
        ["bash", str(DEPLOY_SH), "--checkout", str(checkout), *extra],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_script_exists_and_is_executable() -> None:
    assert DEPLOY_SH.is_file(), f"missing {DEPLOY_SH}"
    assert os.access(DEPLOY_SH, os.X_OK), "scripts/deploy.sh is not executable"


def test_up_to_date_exits_zero(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    proc = _run_deploy(clone)
    assert proc.returncode == 0, proc.stderr
    assert "already up to date" in proc.stdout


def test_fast_forward_deploys_and_exits_zero(tmp_path: Path) -> None:
    origin, clone = _make_origin_and_clone(tmp_path)
    _commit(origin, "hello.txt", "v2\n", "second commit")
    proc = _run_deploy(clone)
    assert proc.returncode == 0, proc.stderr
    assert "deploy: done" in proc.stdout
    assert (clone / "hello.txt").read_text() == "v2\n"
    assert _git(clone, "rev-parse", "HEAD") == _git(origin, "rev-parse", "HEAD")


def test_dry_run_reports_but_does_not_pull(tmp_path: Path) -> None:
    origin, clone = _make_origin_and_clone(tmp_path)
    before = _git(clone, "rev-parse", "HEAD")
    _commit(origin, "hello.txt", "v2\n", "second commit")
    proc = _run_deploy(clone, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "not pulling" in proc.stdout
    assert _git(clone, "rev-parse", "HEAD") == before


def test_untracked_collision_is_friendly_exit_one(tmp_path: Path) -> None:
    # Untracked files deliberately don't block the pull, but git refuses to
    # overwrite one the upstream starts tracking — that refusal must be the
    # documented friendly rc 1, not a raw `set -e` abort.
    origin, clone = _make_origin_and_clone(tmp_path)
    _commit(origin, "new-file.txt", "origin content\n", "adds new-file.txt")
    (clone / "new-file.txt").write_text("local untracked content\n")
    proc = _run_deploy(clone)
    assert proc.returncode == 1
    assert "fast-forward failed" in proc.stderr
    assert (clone / "new-file.txt").read_text() == "local untracked content\n"


def test_foreign_git_env_is_scrubbed(tmp_path: Path) -> None:
    # Run as if from inside another repo's git hook: GIT_DIR/GIT_WORK_TREE point
    # at a FOREIGN repo. Without the env scrub they override `git -C` and the
    # deploy would silently operate on the wrong repository (review-cli#72 bug
    # class). The scrubbed script must still deploy the --checkout repo.
    origin, clone = _make_origin_and_clone(tmp_path)
    _commit(origin, "hello.txt", "v2\n", "second commit")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q", "-b", "main")
    _commit(foreign, "other.txt", "x\n", "foreign commit")
    env = dict(
        _git_env(),
        DRAW_DEPLOY_SKIP_PIPX="1",
        DRAW_DEPLOY_EXPECTED_ORIGIN=_origin_url_of(clone),
        GIT_DIR=str(foreign / ".git"),
        GIT_WORK_TREE=str(foreign),
    )
    proc = subprocess.run(
        ["bash", str(DEPLOY_SH), "--checkout", str(clone)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "deploy: done" in proc.stdout
    assert (clone / "hello.txt").read_text() == "v2\n"
    # The foreign repo must be untouched by the pull.
    assert not (foreign / "hello.txt").exists()


def test_dirty_worktree_refuses(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    (clone / "hello.txt").write_text("local edit\n")
    proc = _run_deploy(clone)
    assert proc.returncode == 1
    assert "local (tracked) changes" in proc.stderr


def test_detached_head_refuses(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    _git(clone, "checkout", "-q", "--detach", "HEAD")
    proc = _run_deploy(clone)
    assert proc.returncode == 1
    assert "detached-HEAD" in proc.stderr


def test_diverged_branch_exits_two(tmp_path: Path) -> None:
    origin, clone = _make_origin_and_clone(tmp_path)
    _commit(origin, "hello.txt", "origin v2\n", "origin-side commit")
    _commit(clone, "local.txt", "local\n", "local-side commit")
    proc = _run_deploy(clone)
    assert proc.returncode == 2
    assert "diverged" in proc.stderr


def test_ahead_of_upstream_exits_zero(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    _commit(clone, "local.txt", "local\n", "local-only commit")
    before = _git(clone, "rev-parse", "HEAD")
    proc = _run_deploy(clone)
    assert proc.returncode == 0, proc.stderr
    assert "ahead" in proc.stdout
    assert _git(clone, "rev-parse", "HEAD") == before


def test_foreign_origin_url_refuses(tmp_path: Path) -> None:
    # An origin re-pointed at a fork must refuse: this script auto-installs what
    # it pulls, so "a remote named origin exists" is not enough — it has to be
    # the OFFICIAL repo (or an explicit DRAW_DEPLOY_EXPECTED_ORIGIN override).
    _, clone = _make_origin_and_clone(tmp_path)
    env = dict(_git_env(), DRAW_DEPLOY_SKIP_PIPX="1")  # no EXPECTED_ORIGIN override
    proc = subprocess.run(
        ["bash", str(DEPLOY_SH), "--checkout", str(clone)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 1
    assert "refusing to auto-install" in proc.stderr


def test_no_arg_default_resolves_own_checkout(tmp_path: Path) -> None:
    # rig invokes `bash <repo>/scripts/deploy.sh` with NO args: the script must
    # deploy the checkout it lives in, not anything resolved from PATH/cwd.
    origin, clone = _make_origin_and_clone(tmp_path)
    scripts_origin = origin / "scripts"
    scripts_origin.mkdir()
    shutil.copy(DEPLOY_SH, scripts_origin / "deploy.sh")
    _git(origin, "add", "scripts/deploy.sh")
    _git(
        origin,
        "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-m", "vendor deploy.sh",
    )
    _git(clone, "pull", "-q", "--ff-only")  # clone now ships its own deploy.sh
    _commit(origin, "hello.txt", "v2\n", "second commit")
    scripts = clone / "scripts"
    env = dict(
        _git_env(),
        DRAW_DEPLOY_SKIP_PIPX="1",
        DRAW_DEPLOY_EXPECTED_ORIGIN=_origin_url_of(clone),
    )
    proc = subprocess.run(
        ["bash", str(scripts / "deploy.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),  # cwd is NOT the checkout — self-location must win
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"checkout = {clone}" in proc.stdout
    assert (clone / "hello.txt").read_text() == "v2\n"


def test_non_origin_remote_refuses(tmp_path: Path) -> None:
    # Deploys are pinned to origin: a checkout whose only remote is a fork must
    # refuse rather than fetch-and-install code from it.
    _, clone = _make_origin_and_clone(tmp_path)
    _git(clone, "remote", "rename", "origin", "fork")
    proc = _run_deploy(clone)
    assert proc.returncode == 1
    assert "no remote 'origin'" in proc.stderr


def test_branch_without_upstream_refuses(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    _git(clone, "checkout", "-q", "-b", "feature/local-only")
    proc = _run_deploy(clone)
    assert proc.returncode == 1
    assert "no upstream" in proc.stderr


def test_repo_without_remote_refuses(tmp_path: Path) -> None:
    repo = tmp_path / "no-remote"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "hello.txt", "v1\n", "initial commit")
    proc = _run_deploy(repo)
    assert proc.returncode == 1
    assert "no remote" in proc.stderr


def _make_fake_pipx(
    tmp_path: Path, reported_version: str, install_rc: int = 0, list_rc: int = 0
) -> tuple[Path, Path]:
    """A stub `pipx` on PATH: `list --short` reports draw-cli at reported_version
    (exiting list_rc), `install` appends its argv to a call log and exits
    install_rc. Lets tests exercise the refresh path without ever touching the
    developer's real pipx venv."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    call_log = tmp_path / "pipx-calls.log"
    stub = bindir / "pipx"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$1" = "list" ]; then echo "draw-cli {reported_version}"; exit {list_rc}; fi\n'
        f'echo "$@" >> "{call_log}"\n'
        f"exit {install_rc}\n"
    )
    stub.chmod(0o755)
    return bindir, call_log


def _run_deploy_with_pipx(checkout: Path, bindir: Path) -> subprocess.CompletedProcess:
    env = _git_env()
    env.pop("DRAW_DEPLOY_SKIP_PIPX", None)
    env["DRAW_DEPLOY_EXPECTED_ORIGIN"] = _origin_url_of(checkout)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", str(DEPLOY_SH), "--checkout", str(checkout)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_pull_refreshes_pipx_install(tmp_path: Path) -> None:
    origin, clone = _make_origin_and_clone(tmp_path)
    _commit(origin, "hello.txt", "v2\n", "second commit")
    bindir, call_log = _make_fake_pipx(tmp_path, "9.9.9")
    proc = _run_deploy_with_pipx(clone, bindir)
    assert proc.returncode == 0, proc.stderr
    assert call_log.exists(), "pipx install was not invoked after the pull"
    assert f"install --force {clone}" in call_log.read_text()


def test_up_to_date_version_drift_resyncs_pipx(tmp_path: Path) -> None:
    _, clone = _make_origin_and_clone(tmp_path)
    # The temp repos have no draw_cli/__init__.py, so plant one (in origin, then
    # ff the clone) with a version the fake pipx does NOT report — an up-to-date
    # checkout paired with a stale venv.
    origin = clone.parent / "origin"
    pkg = origin / "draw_cli"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('__version__ = "1.2.3"\n')
    _git(origin, "add", "draw_cli/__init__.py")
    _git(
        origin,
        "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-m", "add package",
    )
    _git(clone, "pull", "-q", "--ff-only")
    bindir, call_log = _make_fake_pipx(tmp_path, "0.0.1")
    proc = _run_deploy_with_pipx(clone, bindir)
    assert proc.returncode == 0, proc.stderr
    assert "re-syncing" in proc.stdout
    assert call_log.exists() and f"install --force {clone}" in call_log.read_text()


def test_ahead_of_origin_reinstalls_pipx(tmp_path: Path) -> None:
    # Ahead-of-origin means the pipx venv lacks the local commits; the ahead
    # path must reinstall, not declare success over a stale installed tool.
    _, clone = _make_origin_and_clone(tmp_path)
    _commit(clone, "local.txt", "local\n", "local-only commit")
    bindir, call_log = _make_fake_pipx(tmp_path, "0.0.1")
    proc = _run_deploy_with_pipx(clone, bindir)
    assert proc.returncode == 0, proc.stderr
    assert "ahead" in proc.stdout
    assert call_log.exists() and f"install --force {clone}" in call_log.read_text()


def test_ahead_same_version_still_reinstalls_pipx(tmp_path: Path) -> None:
    # A committed local change WITHOUT a version bump is invisible to any
    # version-drift check — ahead must reinstall even when the pipx-reported
    # version equals the checkout's.
    _, clone = _make_origin_and_clone(tmp_path)
    pkg = clone / "draw_cli"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('__version__ = "1.2.3"\n')
    _git(clone, "add", "draw_cli/__init__.py")
    _git(
        clone,
        "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-m", "local change, same version",
    )
    bindir, call_log = _make_fake_pipx(tmp_path, "1.2.3")
    proc = _run_deploy_with_pipx(clone, bindir)
    assert proc.returncode == 0, proc.stderr
    assert "ahead" in proc.stdout
    assert call_log.exists() and f"install --force {clone}" in call_log.read_text()


def test_up_to_date_pipx_list_failure_exits_one(tmp_path: Path) -> None:
    # Even on the up-to-date hot path, a pipx-list failure means the deploy
    # cannot vouch for the installed tool's freshness — fail loudly, don't
    # report success over an unknown pipx state.
    _, clone = _make_origin_and_clone(tmp_path)
    bindir, call_log = _make_fake_pipx(tmp_path, "9.9.9", list_rc=1)
    proc = _run_deploy_with_pipx(clone, bindir)
    assert proc.returncode == 1
    assert "'pipx list' failed" in proc.stderr
    assert not call_log.exists()


def test_tag_named_like_upstream_does_not_confuse_refs(tmp_path: Path) -> None:
    # Regression: a local TAG literally named "origin/main" makes the short ref
    # "origin/main" ambiguous; plumbing must use refs/remotes/... and deploy the
    # real remote-tracking branch, not the tag.
    origin, clone = _make_origin_and_clone(tmp_path)
    _git(clone, "tag", "origin/main", "HEAD")
    _commit(origin, "hello.txt", "v2\n", "second commit")
    proc = _run_deploy(clone)
    assert proc.returncode == 0, proc.stderr
    assert "deploy: done" in proc.stdout
    assert (clone / "hello.txt").read_text() == "v2\n"


def test_skill_refresh_failure_warns_but_exits_zero(tmp_path: Path) -> None:
    # A failed install-skill is a warning, not a deploy failure: the code IS
    # deployed; only the skill registration needs a manual re-run.
    origin, clone = _make_origin_and_clone(tmp_path)
    bindir_origin = origin / "bin"
    bindir_origin.mkdir()
    shim = bindir_origin / "draw"
    shim.write_text("#!/usr/bin/env bash\nexit 1\n")
    shim.chmod(0o755)
    _git(origin, "add", "bin/draw")
    _git(
        origin,
        "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-m", "add failing draw shim",
    )
    proc = _run_deploy(clone)
    assert proc.returncode == 0, proc.stderr
    assert "deploy: done" in proc.stdout
    assert "install-skill' failed" in proc.stderr


def test_pipx_install_failure_exits_one(tmp_path: Path) -> None:
    # A failed reinstall must be a loud rc-1 failure — the user would otherwise
    # keep running the OLD version while believing the deploy succeeded.
    origin, clone = _make_origin_and_clone(tmp_path)
    _commit(origin, "hello.txt", "v2\n", "second commit")
    bindir, call_log = _make_fake_pipx(tmp_path, "9.9.9", install_rc=1)
    proc = _run_deploy_with_pipx(clone, bindir)
    assert proc.returncode == 1
    assert "pipx install --force failed" in proc.stderr
    assert call_log.exists()


def test_pipx_list_failure_after_pull_exits_one(tmp_path: Path) -> None:
    # pipx present but `pipx list` erroring is NOT "not installed": skipping the
    # reinstall on it would exit 0 with the running `draw` silently stale.
    origin, clone = _make_origin_and_clone(tmp_path)
    _commit(origin, "hello.txt", "v2\n", "second commit")
    bindir, call_log = _make_fake_pipx(tmp_path, "9.9.9", list_rc=1)
    proc = _run_deploy_with_pipx(clone, bindir)
    assert proc.returncode == 1
    assert "'pipx list' failed" in proc.stderr
    assert not call_log.exists(), "install must not run on an unreadable pipx state"


def test_pull_refreshes_skill(tmp_path: Path) -> None:
    # A checkout that ships bin/draw gets `install-skill` re-run after the pull.
    origin, clone = _make_origin_and_clone(tmp_path)
    skill_log = tmp_path / "skill-calls.log"
    bindir_origin = origin / "bin"
    bindir_origin.mkdir()
    shim = bindir_origin / "draw"
    shim.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{skill_log}"\nexit 0\n')
    shim.chmod(0o755)
    _git(origin, "add", "bin/draw")
    _git(
        origin,
        "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-m", "add draw shim",
    )
    proc = _run_deploy(clone)
    assert proc.returncode == 0, proc.stderr
    assert "refreshed draw skill" in proc.stdout
    assert skill_log.exists() and "install-skill" in skill_log.read_text()


def test_no_pipx_on_path_is_fine(tmp_path: Path) -> None:
    origin, clone = _make_origin_and_clone(tmp_path)
    _commit(origin, "hello.txt", "v2\n", "second commit")
    # An empty fake bindir shadows nothing; strip pipx by pointing PATH at a
    # minimal toolset instead: symlink just the binaries deploy.sh needs.
    bindir = tmp_path / "minbin"
    bindir.mkdir()
    for tool in ("bash", "git", "sed", "awk", "head", "dirname", "cat", "env", "readlink"):
        src = subprocess.run(
            ["which", tool], capture_output=True, text=True
        ).stdout.strip()
        if src:
            (bindir / tool).symlink_to(src)
    env = _git_env()
    env.pop("DRAW_DEPLOY_SKIP_PIPX", None)
    env["DRAW_DEPLOY_EXPECTED_ORIGIN"] = _origin_url_of(clone)
    env["PATH"] = str(bindir)
    # Resolve bash absolutely: the child's exec must not depend on the minimal PATH.
    bash = shutil.which("bash") or "/bin/bash"
    proc = subprocess.run(
        [bash, str(DEPLOY_SH), "--checkout", str(clone)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "pipx not on PATH" in proc.stdout


def test_not_a_git_checkout_refuses(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    proc = _run_deploy(plain)
    assert proc.returncode == 1
    assert "not a git checkout" in proc.stderr


def test_unknown_argument_is_usage_error() -> None:
    env = dict(_git_env(), DRAW_DEPLOY_SKIP_PIPX="1")
    proc = subprocess.run(
        ["bash", str(DEPLOY_SH), "--bogus"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 1
    assert "unknown argument" in proc.stderr


if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, "-m", "pytest", __file__, "-q"]).returncode)
