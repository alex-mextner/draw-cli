"""draw — generate images via Hugging Face or a ChatGPT subscription.

Usage:
    draw "a cute robot" -o robot.png
    draw "a cute robot" --model black-forest-labs/FLUX.1-schnell -o robot.png
    echo "a cute robot" | draw -o robot.png
    draw "a cute robot" --provider chatgpt -o robot.png
    draw --provider chatgpt --check
    draw --version

Env:
    DRAW_PROVIDER   hf (default), chatgpt or codex
    DRAW_CODEX_BIN  Path to Codex CLI (default: codex)
    HF_TOKEN        Hugging Face access token (HF provider only)
    HF_MODEL        Default model (default: black-forest-labs/FLUX.1-schnell)
"""
from __future__ import annotations

import argparse
import os
import sys

from draw_cli import __version__

DEFAULT_MODEL = "black-forest-labs/FLUX.1-schnell"
ENV_FILE = os.path.expanduser("~/.config/draw-cli/.env")


def _load_env() -> None:
    if not os.path.isfile(ENV_FILE):
        return
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.split("#", 1)[0].strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _token() -> str:
    token = (os.environ.get("HF_TOKEN") or "").strip()
    if not token:
        sys.stderr.write("draw: HF_TOKEN env var is required\n")
        sys.exit(1)
    return token


def _default_model() -> str:
    return os.environ.get("HF_MODEL", DEFAULT_MODEL)


# --- install-skill: make agent harnesses aware this tool exists ----------------
# Writes a SKILL.md (Agent Skills standard, ~/.agents/skills/) read by Claude
# Code, Codex, opencode, Gemini, Cursor; a short always-on blurb into each
# detected harness's global instruction file; and an idempotent SessionStart
# hook that surfaces every installed agent-CLI at the top of each session.

SKILL_NAME = "draw"
SKILL_MD = """\
---
name: draw
description: >-
  Generate images via Hugging Face (FLUX by default) or a ChatGPT subscription
  through the local Codex CLI (--provider chatgpt, no API key). Use when
  a task needs an image created from a description — placeholder or hero art, icons,
  concept sketches, mock assets, a diagram rendered as an image — produced from the
  shell without leaving the session, e.g. `draw "a cute robot" -o robot.png`.
metadata:
  author: alex-mextner
  repo: https://github.com/alex-mextner/draw-cli
---

# draw — text-to-image from the CLI

Generate an image from any agent or shell via Hugging Face or ChatGPT/Codex.

## Invocation
```
draw "a cute robot" -o robot.png        # prompt + output path (required)
draw "..." --model <hf-id> -o out.png   # pick a Hugging Face model
echo "a prompt" | draw -o out.png       # prompt from stdin
draw "..." --provider chatgpt -o out.png # ChatGPT subscription, no API key
draw "..." --provider chatgpt -i ref.png -o out.png # reference/edit
draw --provider chatgpt --check         # local setup check, no generation
```

## When to use
- The task needs a generated image/asset (placeholder, hero, icon, concept art).
- You want to produce art inline without leaving the shell session.

HF needs `HF_TOKEN` (auto-loaded from `~/.config/draw-cli/.env`). ChatGPT needs
current Codex CLI signed in with `codex login` using ChatGPT, not an API key.
Set `DRAW_PROVIDER=chatgpt` in that .env to make it the default. Codex manages
the image model: do not pass gpt-image IDs to --model or --codex-model.
Image/version availability and limits follow the account rollout. Never fall
back to a paid API or call draw recursively from its own Codex subprocess.
Pair with `tg --photo out.png "caption"` to send the result to Telegram.
"""
SKILL_BLURB = (
    '`draw` — generate an image: `draw "prompt" -o out.png` (Hugging Face), '
    'or add `--provider chatgpt` for a ChatGPT subscription via Codex (no API key). '
    'Use when a task needs a generated image/asset; never invoke recursively.'
)

_HOOK_MARKER = "# agent-tools-awareness"
_HOOK_COMMAND = (
    "sh -c 'd=\"$HOME/.agents/skills/.blurbs\"; ls \"$d\"/*.md >/dev/null 2>&1 && "
    '{ printf \"Agent CLI tools installed on this machine (prefer them):\\n\"; '
    "cat \"$d\"/*.md; }' " + _HOOK_MARKER
)


def _detected(cmd: str, *dirs: str) -> bool:
    import shutil
    if shutil.which(cmd):
        return True
    return any(os.path.isdir(os.path.expanduser(d)) for d in dirs)


def _append_marked(path, tool: str, blurb: str) -> None:
    import re
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    start, end = f"<!-- skill:{tool} -->", f"<!-- /skill:{tool} -->"
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    existing = re.sub(re.escape(start) + r".*?" + re.escape(end) + r"\n?", "", existing, flags=re.S)
    block = f"{start}\n{blurb}\n{end}\n"
    p.write_text((existing.rstrip() + "\n\n" + block) if existing.strip() else block, encoding="utf-8")


def _ensure_sessionstart_hook(home) -> bool:
    """Idempotently add a SessionStart hook to ~/.claude/settings.json that
    surfaces installed agent CLIs. Returns True if settings were changed.
    Conservative: never removes or rewrites unrelated config; backs up first."""
    import json
    from pathlib import Path
    settings = Path(home) / ".claude" / "settings.json"
    if not settings.parent.is_dir():
        return False
    try:
        data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    except (json.JSONDecodeError, OSError):
        return False  # don't clobber a file we can't parse
    if not isinstance(data, dict):
        return False
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return False
    sessionstart = hooks.setdefault("SessionStart", [])
    if not isinstance(sessionstart, list):
        return False
    # Already installed? (match our marker anywhere in existing commands)
    for group in sessionstart:
        for h in (group or {}).get("hooks", []) if isinstance(group, dict) else []:
            if isinstance(h, dict) and _HOOK_MARKER in str(h.get("command", "")):
                return False
    sessionstart.append({"hooks": [{"type": "command", "command": _HOOK_COMMAND}]})
    if settings.exists():
        settings.with_suffix(".json.bak").write_text(settings.read_text(encoding="utf-8"), encoding="utf-8")
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def install_agent_skill(name: str, skill_md: str, blurb: str) -> int:
    from pathlib import Path
    home = Path.home()
    written = []

    # Layer 1 — SKILL.md (Agent Skills standard) + blurb file for the hook.
    skill_dir = home / ".agents" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    written.append(str(skill_dir / "SKILL.md"))
    blurbs = home / ".agents" / "skills" / ".blurbs"
    blurbs.mkdir(parents=True, exist_ok=True)
    (blurbs / f"{name}.md").write_text(f"- {blurb}\n", encoding="utf-8")

    # Claude Code also scans ~/.claude/skills — symlink for compatibility.
    claude_skills = home / ".claude" / "skills"
    if claude_skills.is_dir():
        link = claude_skills / name
        if not link.exists():
            try:
                link.symlink_to(Path("..") / ".." / ".agents" / "skills" / name)
            except OSError:
                pass

    # Layer 2 — always-on blurb in each DETECTED harness's instruction file.
    harness_files = [
        ("claude", home / ".claude" / "CLAUDE.md", ("~/.claude",)),
        ("codex", home / ".codex" / "AGENTS.md", ("~/.codex",)),
        ("opencode", home / ".config" / "opencode" / "AGENTS.md", ("~/.config/opencode",)),
        ("gemini", home / ".gemini" / "GEMINI.md", ("~/.gemini",)),
    ]
    for cmd, path, dirs in harness_files:
        if _detected(cmd, *dirs):
            _append_marked(path, name, blurb)
            written.append(str(path))

    # Layer 3 — SessionStart hook (Claude Code) aggregating all installed tools.
    if (home / ".claude").is_dir():
        if _ensure_sessionstart_hook(home):
            written.append("SessionStart hook -> ~/.claude/settings.json")

    for w in written:
        print(f"  ✓ {w}")
    print(f"{name}: install-skill done ({len(written)} target(s)). Re-run anytime; idempotent.")
    return 0


def install_skill() -> int:
    return install_agent_skill(SKILL_NAME, SKILL_MD, SKILL_BLURB)


def generate(prompt: str, model: str, out_path: str) -> None:
    try:
        from huggingface_hub import InferenceClient
    except ImportError:
        sys.stderr.write(
            "draw: missing deps. Install via pipx (isolated): "
            "pipx install --force git+https://github.com/alex-mextner/draw-cli\n"
            "  — or: python3 -m pip install --user huggingface_hub Pillow\n"
        )
        sys.exit(1)

    token = _token()
    client = InferenceClient(token=token)
    try:
        image = client.text_to_image(prompt, model=model)
    except Exception as e:
        sys.stderr.write(f"draw: generation failed: {e}\n")
        sys.exit(1)

    try:
        image.save(out_path)
    except (OSError, ValueError) as e:
        # PIL raises ValueError when the output path has no/unknown extension
        # (can't infer the format), OSError for filesystem/encode failures.
        sys.stderr.write(f"draw: cannot save {out_path}: {e}\n")
        sys.exit(1)
    print(f"draw: saved {out_path}")


def main() -> int:
    if sys.argv[1:] == ["install-skill"]:  # exact-match: `draw "install-skill" -o x` still draws
        return install_skill()
    _load_env()
    ap = argparse.ArgumentParser(description="Generate an image from a text prompt")
    # action="version" short-circuits before required-arg validation, so
    # `draw --version` works without -o. __version__ is the single source of truth
    # (kept in sync with pyproject's [project] version).
    ap.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"draw {__version__}",
        help="show program version and exit",
    )
    ap.add_argument("prompt", nargs="?", help="text prompt (or read from stdin)")
    ap.add_argument("-o", "--out", help="output image path (required unless --check)")
    ap.add_argument("--provider", choices=("hf", "chatgpt", "codex"),
                    default=os.environ.get("DRAW_PROVIDER", "hf"),
                    help="hf (default) or ChatGPT subscription via local Codex CLI")
    ap.add_argument("--model", help="HF model id (HF_MODEL or FLUX by default; HF only)")
    ap.add_argument("--codex-bin", default=os.environ.get("DRAW_CODEX_BIN", "codex"),
                    help="Codex executable path/name (ChatGPT provider only)")
    ap.add_argument("--codex-model", default=os.environ.get("DRAW_CODEX_MODEL"),
                    help="Codex reasoning model, NOT the image model (normally omit)")
    ap.add_argument("-i", "--image", action="append", default=[],
                    help="reference/edit image; repeat up to five times (ChatGPT only)")
    ap.add_argument("--timeout", type=int, default=600,
                    help="Codex generation timeout in seconds (default: 600)")
    ap.add_argument("--check", action="store_true",
                    help="check Codex installation/login without generating an image")
    args = ap.parse_args()

    if args.provider not in {"hf", "chatgpt", "codex"}:
        ap.error("DRAW_PROVIDER must be hf, chatgpt or codex")
    if args.timeout <= 0:
        ap.error("--timeout must be positive")
    if args.provider != "hf" and args.model is not None:
        ap.error("--model is HF-only. Codex manages the ChatGPT image model; "
                 "omit --model (GPT Image 2.5/Flare/Sunburst cannot be pinned here).")
    if args.provider == "hf" and (args.image or args.check):
        ap.error("--image and --check require --provider chatgpt (or codex)")
    if args.check:
        if args.prompt or args.out or args.image:
            ap.error("--check does not accept a prompt, output path or reference images")
        from draw_cli.codex import CodexError, inspect_codex
        try:
            installation = inspect_codex(args.codex_bin)
        except (CodexError, OSError) as exc:
            sys.stderr.write(f"draw: {exc}\n")
            return 1
        print(f"draw: {installation.version} ({installation.binary})")
        print("draw: ChatGPT login and native image-generation client support detected.")
        print("draw: no generation performed; model rollout/plan quota not verified. "
              "The image model is managed by Codex, not pinned by draw.")
        return 0
    if not args.out:
        ap.error("the following arguments are required: -o/--out")

    prompt = args.prompt
    if not prompt:
        if not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
        if not prompt:
            ap.error("prompt is required (arg or stdin)")

    if args.provider == "hf":
        generate(prompt, args.model or _default_model(), args.out)
    else:
        from draw_cli.codex import CodexError, generate as generate_codex
        try:
            sys.stderr.write("draw: using ChatGPT subscription via Codex; image model is Codex-managed, not pinned.\n")
            generate_codex(prompt, args.out, binary=args.codex_bin, model=args.codex_model,
                           references=args.image, timeout=args.timeout)
        except KeyboardInterrupt:
            sys.stderr.write("draw: cancelled; local Codex process stopped.\n")
            return 130
        except (CodexError, OSError) as exc:
            sys.stderr.write(f"draw: {exc}\n")
            return 1
        print(f"draw: saved {args.out} (ChatGPT subscription via Codex)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
