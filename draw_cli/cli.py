"""draw — text-to-image via Hugging Face, Stability API, or local SD 3.5.

Usage:
    draw "a cute robot" -o robot.png
    draw "a cute robot" --model black-forest-labs/FLUX.1-schnell -o robot.png
    echo "a cute robot" | draw -o robot.png
    draw "a cute robot" --backend local --model sd3.5 -o robot.png
    draw --check-resources
    draw --version

Env:
    HF_TOKEN        Hugging Face token (API, or gated model downloads)
    STABILITY_API_KEY  Stability AI API key
    DRAW_BACKEND    Default backend: hf, stability, local (default: hf)
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
  Generate images via Hugging Face (FLUX by default), Stability API, or local
  Stable Diffusion 3.5 Large with resource checks. Use when
  a task needs an image created from a description — placeholder or hero art, icons,
  concept sketches, mock assets, a diagram rendered as an image — produced from the
  shell without leaving the session, e.g. `draw "a cute robot" -o robot.png`.
metadata:
  author: alex-mextner
  repo: https://github.com/alex-mextner/draw-cli
---

# draw — text-to-image from the CLI

Generate an image from any agent or shell via an API or local SD 3.5 Large.

## Invocation
```
draw "a cute robot" -o robot.png        # prompt + output path (required)
draw "..." --model <hf-id> -o out.png   # pick a Hugging Face model
echo "a prompt" | draw -o out.png       # prompt from stdin
draw "..." --model sd3.5 --provider replicate -o out.png  # HF API
draw "..." --backend stability -o out.png                # Stability API
draw --check-resources --json                            # no weight download
draw "..." --backend local --offload auto -o out.png      # local SD 3.5
```

## When to use
- The task needs a generated image/asset (placeholder, hero, icon, concept art).
- You want to produce art inline without leaving the shell session.

API credentials: `HF_TOKEN` for Hugging Face, `STABILITY_API_KEY` for Stability.
Both are auto-loaded from `~/.config/draw-cli/.env`. API use can incur charges.
Local mode needs the optional `[local]` dependencies, accepted Hugging Face model
access and enough available disk/RAM/VRAM. Run `draw --check-resources` first;
use `--cache-dir` to select a disk. `--offline` uses a cache downloaded by draw.
CPU inference requires explicit `--device cpu`. Never switch backends, download
large weights, or make paid API calls without the user requesting that mode.
Pair with
`tg --photo out.png "caption"` to send the result to Telegram.
"""
SKILL_BLURB = (
    '`draw` — generate an image via Hugging Face, Stability API or local SD 3.5: '
    '`draw "prompt" -o out.png`. Use when a task needs a generated image/asset.'
)

# Keep the pre-existing agent-harness installation behavior isolated and unchanged.
from draw_cli.legacy import install_agent_skill


def install_skill() -> int:
    return install_agent_skill(SKILL_NAME, SKILL_MD, SKILL_BLURB)


def generate(prompt: str, model: str, out_path: str, *, backend: str = "hf", **options) -> None:
    from draw_cli.backends import (DrawError, SD35_MODEL, generate_hf, generate_stability,
                                   normalize_model, save_image, validate_output)

    model = normalize_model(model)
    settings = options.get("settings")
    width = settings.width if settings is not None else options.get("width") or 1024
    height = settings.height if settings is not None else options.get("height") or 1024
    # Fail before a paid request or model download for invalid/unwritable destinations.
    output = validate_output(out_path, width, height)
    if backend == "local":
        from draw_cli.local import generate_local
        if model != SD35_MODEL:
            raise DrawError("local mode currently supports only --model sd3.5 (SD 3.5 Large)")
        image = generate_local(prompt, **options)
    elif backend == "stability":
        image = generate_stability(prompt, model, **options)
    elif backend == "hf":
        image = generate_hf(prompt, model, **options)
    else:
        raise DrawError(f"unsupported backend: {backend}")
    save_image(image, str(output))
    print(f"draw: saved {out_path}")


def _int_range(low: int, high: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("expected an integer") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return number
    return parse


def _finite_float(value: str) -> float:
    import math
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a number") from None
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite, non-negative number")
    return number


def main() -> int:
    if sys.argv[1:] == ["install-skill"]:
        return install_skill()
    _load_env()
    from draw_cli.backends import ASPECT_RATIOS, SD35_MODEL, normalize_model, safe_error

    ap = argparse.ArgumentParser(description="Generate an image via an API or local Stable Diffusion 3.5")
    ap.add_argument("-V", "--version", action="version", version=f"draw {__version__}")
    ap.add_argument("prompt", nargs="?", help="text prompt (or read from stdin)")
    ap.add_argument("-o", "--out", help="output image path (required except for resource checks)")
    ap.add_argument("--backend", choices=("hf", "api", "stability", "local"),
                    help="hf/api: Hugging Face; stability: Stability AI; local: Diffusers")
    ap.add_argument("--model", help="HF model ID or sd3.5 alias; local/stability default to SD 3.5 Large")
    ap.add_argument("--provider", help="Hugging Face Inference Provider (default: auto)")
    ap.add_argument("--negative-prompt")
    ap.add_argument("--seed", type=_int_range(0, 2**32 - 1))
    ap.add_argument("--width", type=_int_range(256, 2048))
    ap.add_argument("--height", type=_int_range(256, 2048))
    ap.add_argument("--steps", type=_int_range(1, 100), help="HF/local steps (local default: 28)")
    ap.add_argument("--guidance-scale", type=_finite_float, help="HF/local guidance (local default: 3.5)")
    ap.add_argument("--aspect-ratio", choices=ASPECT_RATIOS, help="Stability API only (default: 1:1)")
    ap.add_argument("--timeout", type=_finite_float, help="API timeout in seconds (default: 300)")
    ap.add_argument("--device", help="local: auto, cuda, cuda:N, mps or cpu")
    ap.add_argument("--dtype", choices=("auto", "float16", "bfloat16", "float32"))
    ap.add_argument("--offload", choices=("auto", "none", "model", "sequential"))
    ap.add_argument("--cache-dir", help="local model cache directory; otherwise use HF_HUB_CACHE")
    ap.add_argument("--revision", help="local model revision to pin (default: main)")
    ap.add_argument("--offline", action="store_true", help="local: use only a previously downloaded draw cache")
    ap.add_argument("--check-resources", action="store_true", help="local preflight only, no weights or generation")
    ap.add_argument("--json", action="store_true", help="machine-readable --check-resources report")
    args = ap.parse_args()

    backend = args.backend or os.environ.get("DRAW_BACKEND") or ("local" if args.check_resources else "hf")
    backend = "hf" if backend == "api" else backend
    if backend not in ("hf", "stability", "local"):
        ap.error("DRAW_BACKEND must be hf, api, stability or local")
    if args.check_resources and backend != "local":
        ap.error("--check-resources requires --backend local")
    if args.json and not args.check_resources:
        ap.error("--json requires --check-resources")
    local_flags = (args.device, args.dtype, args.offload, args.cache_dir, args.revision, args.offline)
    if backend != "local" and any(local_flags):
        ap.error("--device/--dtype/--offload/--cache-dir/--revision/--offline require --backend local")
    if backend != "hf" and args.provider is not None:
        ap.error("--provider applies only to --backend hf")
    if backend != "stability" and args.aspect_ratio is not None:
        ap.error("--aspect-ratio applies only to --backend stability; use --width and --height otherwise")
    if backend == "stability" and any(v is not None for v in (
            args.width, args.height, args.steps, args.guidance_scale)):
        ap.error("Stability API mode does not expose --width/--height/--steps/--guidance-scale; "
                 "use --aspect-ratio, or choose hf/local")
    if args.timeout is not None and (backend == "local" or args.timeout <= 0):
        ap.error("--timeout must be positive and applies only to API backends")
    model = normalize_model(args.model or (_default_model() if backend == "hf" else SD35_MODEL))
    if backend != "hf" and model != SD35_MODEL:
        ap.error("local/stability mode currently supports only --model sd3.5 (SD 3.5 Large)")

    settings = None
    if backend == "local":
        import re
        from draw_cli.local import LocalSettings
        device = args.device or "auto"
        if device not in ("auto", "cpu", "mps", "cuda") and not re.fullmatch(r"cuda:\d+", device):
            ap.error("--device must be auto, cpu, mps, cuda or cuda:N")
        width, height = args.width or 1024, args.height or 1024
        if width % 16 or height % 16:
            ap.error("local --width and --height must be divisible by 16")
        offline = args.offline or os.environ.get("HF_HUB_OFFLINE", "").upper() in ("1", "ON", "YES", "TRUE")
        settings = LocalSettings(device=device, dtype=args.dtype or "auto", offload=args.offload or "auto",
                                 cache_dir=args.cache_dir, revision=args.revision or "main", offline=offline,
                                 width=width, height=height)
    try:
        if args.check_resources:
            import json
            from draw_cli.local import format_report, preflight
            report, _ = preflight(settings)
            if args.out:
                from draw_cli.backends import validate_output
                try:
                    validate_output(args.out, settings.width, settings.height)
                except Exception as exc:
                    report["errors"].append(safe_error(exc))
                    report["ok"] = False
            print(json.dumps(report, indent=2) if args.json else format_report(report))
            return 0 if report["ok"] else 1
        if not args.out:
            ap.error("-o/--out is required for generation")
        prompt = args.prompt
        if not prompt and not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
        if not prompt or not prompt.strip():
            ap.error("prompt is required (arg or stdin)")
        common = dict(seed=args.seed, negative_prompt=args.negative_prompt)
        if backend == "local":
            common.update(settings=settings, steps=args.steps if args.steps is not None else 28,
                          guidance_scale=args.guidance_scale if args.guidance_scale is not None else 3.5)
        elif backend == "stability":
            common.update(aspect_ratio=args.aspect_ratio or "1:1", timeout=args.timeout or 300)
        else:
            common.update(provider=args.provider or "auto", timeout=args.timeout or 300,
                          width=args.width, height=args.height, steps=args.steps, guidance_scale=args.guidance_scale)
        generate(prompt, model, args.out, backend=backend, **common)
        return 0
    except KeyboardInterrupt:
        sys.stderr.write("draw: cancelled\n")
        return 130
    except Exception as exc:
        sys.stderr.write(f"draw: {safe_error(exc)}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
