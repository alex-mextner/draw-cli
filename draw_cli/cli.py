"""draw — generate images via Hugging Face, Stability API, local Diffusers, native
Metal/GGUF, or ChatGPT through Codex or its browser UI.

Usage:
    draw "a cute robot" -o robot.png
    draw "a cute robot" --model black-forest-labs/FLUX.1-schnell -o robot.png
    echo "a cute robot" | draw -o robot.png
    draw "a cute robot" --backend chatgpt -o robot.png
    draw --backend chatgpt --check
    draw login
    draw --backend chatgpt-web "a cute robot" -o robot.jpg
    draw --version

Env:
    DRAW_BACKEND    hf (default), api, stability, local, sdcpp, chatgpt, codex or chatgpt-web
    DRAW_CODEX_BIN  Path to Codex CLI (default: codex)
    HF_TOKEN        Hugging Face access token (HF backend only)
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


def _option_present(raw_args: list[str], option: str) -> bool:
    """Whether an option was explicitly supplied, including --flag=value."""
    return any(arg == option or arg.startswith(option + "=") for arg in raw_args)


SKILL_NAME = "draw"
SKILL_MD = """\
---
name: draw
description: >-
  Generate images via Hugging Face (FLUX default), Stability API, local
  Diffusers/stable-diffusion.cpp (Metal/GGUF), or a ChatGPT subscription through Codex
  CLI or its browser UI (chatgpt-web, draw login). Use to create an image in the
  shell, e.g. `draw "a cute robot" -o robot.png`.
metadata:
  author: alex-mextner
  repo: https://github.com/alex-mextner/draw-cli
---

# draw — text-to-image from the CLI

```
draw "a cute robot" -o robot.png
echo "a prompt" | draw -o robot.png
draw "..." --model sd3.5 --provider replicate -o out.png
draw "..." --backend stability -o out.png
draw --check-resources --json
draw "..." --backend local --offload auto -o out.png
draw --backend sdcpp --quantization q4_0 --check-resources --json
draw "..." --backend sdcpp --quantization q4_0 --seed 42 -o out.png
draw "..." --backend chatgpt -o out.png              # ChatGPT subscription, no API key
draw "..." --backend chatgpt -i ref.png -o out.png    # reference/edit
draw --backend chatgpt --check                        # local Codex setup check
draw login                                          # direct ChatGPT browser sign-in
draw "..." --backend chatgpt-web -o out.jpg           # direct browser, not Codex
```

API credentials: HF_TOKEN for Hugging Face, STABILITY_API_KEY for Stability.
Both are auto-loaded from ~/.config/draw-cli/.env. API use can incur charges.
Local Diffusers requires the [local] extra. The sdcpp backend requires a recent
external sd-cli with Metal enabled and the lightweight [sdcpp] extra, NOT torch.
GGUF presets quantize both the denoiser and T5, which can change output quality.
No benchmark on a real Mac is implied by support or resource estimates.

Run --check-resources first; it downloads no model weights. Use --cache-dir to
select a disk. --offline reuses a cache previously prepared by draw with the same
backend, preset and revision. Generation downloads only missing selected weights.
CPU inference requires explicit --device cpu. Do not change system GPU memory
limits, switch backends, download weights or make paid calls without the user
requesting that mode. See docs/apple-silicon.md for native engine setup and
comparison with Draw Things CLI and MLX alternatives.

ChatGPT (--backend chatgpt/codex) needs the current Codex CLI signed in with
`codex login` using a ChatGPT plan, not an API key. Set DRAW_BACKEND=chatgpt in
that .env to make it the default. Codex manages the image model: do not pass
gpt-image IDs to --model or --codex-model. Image/version availability and
limits follow the account rollout. Never fall back to a paid API or call draw
recursively from its own Codex subprocess.
Direct ChatGPT UI mode: install the [browser] extra, run `draw login`, then use
`--backend chatgpt-web`. A dedicated browser profile persists the user's session;
no Desktop credentials or cookies are exported. Generation is headless unless
--headed is supplied. Stop on quota, login or human checks; never bypass them.
This experimental UI adapter may need updates when ChatGPT changes its controls.
Pair with `tg --photo out.png
"caption"` to send the result to Telegram.
"""
SKILL_BLURB = (
    '`draw` — generate an image via APIs, local Diffusers, Metal/GGUF, or a '
    'ChatGPT subscription via Codex (`--backend chatgpt`, no API key): '
    '`draw "prompt" -o out.png`. Use when a task needs a generated image/asset; '
    'never invoke recursively.'
)

# Preserve the existing agent-harness installation behavior.
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
    output = validate_output(out_path, width, height)
    if backend in ("local", "sdcpp") and model != SD35_MODEL:
        raise DrawError("local engines currently support only --model sd3.5 (SD 3.5 Large)")
    if backend == "local":
        from draw_cli.local import generate_local
        image = generate_local(prompt, **options)
    elif backend == "sdcpp":
        from draw_cli.sdcpp import generate_sdcpp
        image = generate_sdcpp(prompt, scratch_dir=str(output.parent), **options)
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
    raw_args = list(sys.argv[1:])
    if raw_args and raw_args[0] == "app-session":
        from draw_cli.app_session import main as app_session_main
        return app_session_main(raw_args[1:])
    _load_env()
    if raw_args and raw_args[0] == "login":
        login = argparse.ArgumentParser(prog="draw login", description="Sign in manually in draw's dedicated ChatGPT browser", allow_abbrev=False)
        login.add_argument("--backend", choices=("chatgpt-web",), default="chatgpt-web")
        login.add_argument("--browser-profile", default=os.environ.get("DRAW_BROWSER_PROFILE"))
        login.add_argument("--browser-channel", choices=("chrome", "chromium"), default=os.environ.get("DRAW_BROWSER_CHANNEL", "chrome"))
        login.add_argument("--timeout", type=_finite_float, default=600)
        options = login.parse_args(raw_args[1:])
        from draw_cli.chatgpt_web import run
        return run(mode="login", profile=options.browser_profile, channel=options.browser_channel, timeout=options.timeout)
    from draw_cli.backends import ASPECT_RATIOS, SD35_MODEL, normalize_model, safe_error
    ap = argparse.ArgumentParser(description="Generate images via APIs, Diffusers, Metal/GGUF, or ChatGPT",
                                 allow_abbrev=False)
    ap.add_argument("-V", "--version", action="version", version=f"draw {__version__}")
    ap.add_argument("prompt", nargs="?", help="text prompt (or read from stdin)")
    ap.add_argument("-o", "--out", help="output image path (required except for resource/browser/Codex checks)")
    ap.add_argument("--backend", choices=("hf", "api", "stability", "local", "sdcpp", "chatgpt", "codex", "chatgpt-web"),
                    help="hf/api: Hugging Face; stability: Stability AI; local: Diffusers; "
                         "sdcpp: native GGUF; codex: ChatGPT plan via Codex CLI; "
                         "chatgpt: deprecated alias of codex; chatgpt-web: experimental browser UI")
    ap.add_argument("--model", help="HF model ID or sd3.5 alias; non-HF backends default to SD 3.5 Large; "
                                    "omit for chatgpt/codex (Codex manages the image model)")
    ap.add_argument("--provider", help="Hugging Face Inference Provider (default: auto)")
    ap.add_argument("--negative-prompt")
    ap.add_argument("--seed", type=_int_range(0, 2**32 - 1))
    ap.add_argument("--width", type=_int_range(256, 2048))
    ap.add_argument("--height", type=_int_range(256, 2048))
    ap.add_argument("--steps", type=_int_range(1, 100), help="HF/local steps (local engines default: 28)")
    ap.add_argument("--guidance-scale", type=_finite_float, help="HF/local guidance (local engines default: 3.5)")
    ap.add_argument("--aspect-ratio", choices=ASPECT_RATIOS, help="Stability API only (default: 1:1)")
    ap.add_argument("--timeout", type=_finite_float,
                    help="API/Codex timeout in seconds (default: 300 API, 600 chatgpt/codex)")
    ap.add_argument("--device", help="Diffusers: auto/cuda/cuda:N/mps/cpu; sdcpp: auto/metal/mps/cpu")
    ap.add_argument("--dtype", choices=("auto", "float16", "bfloat16", "float32"))
    ap.add_argument("--offload", choices=("auto", "none", "model", "sequential"))
    ap.add_argument("--cache-dir", help="model cache directory; otherwise use HF_HUB_CACHE")
    ap.add_argument("--revision", help="local backend's model repository revision (default: main)")
    ap.add_argument("--offline", action="store_true", help="use only a previously downloaded draw cache")
    ap.add_argument("--check-resources", action="store_true", help="preflight only, no weights or generation")
    ap.add_argument("--json", action="store_true", help="machine-readable --check-resources report")
    ap.add_argument("--sdcpp-bin", help="external sd-cli path (or DRAW_SDCPP_BIN)")
    ap.add_argument("--quantization", choices=("q4_0", "q5_0", "q8_0"), help="sdcpp preset (default: q4_0)")
    ap.add_argument("--sdcpp-memory", choices=("resident", "disk"), help="native weight residency (default: resident)")
    ap.add_argument("--no-flash-attention", action="store_true", help="sdcpp: disable diffusion Flash Attention")
    ap.add_argument("--native-timeout", type=_finite_float, help="sd-cli time limit, including load (default: 3600s)")
    ap.add_argument("--codex-bin", default=os.environ.get("DRAW_CODEX_BIN", "codex"),
                    help="Codex executable path/name (chatgpt/codex backend only)")
    ap.add_argument("--codex-model", default=os.environ.get("DRAW_CODEX_MODEL"),
                    help="Codex reasoning model, NOT the image model (normally omit)")
    ap.add_argument("-i", "--image", action="append", default=[],
                    help="reference/edit image; repeat up to five times (chatgpt/codex only)")
    ap.add_argument("--check", action="store_true",
                    help="check login without generating an image (chatgpt/codex: local; chatgpt-web: opens browser)")
    ap.add_argument("--browser-profile", default=os.environ.get("DRAW_BROWSER_PROFILE"),
                    help="dedicated draw browser profile, never a personal Chrome profile (chatgpt-web only)")
    ap.add_argument("--browser-channel", choices=("chrome", "chromium"),
                    default=os.environ.get("DRAW_BROWSER_CHANNEL", "chrome"), help="browser engine (chatgpt-web only)")
    ap.add_argument("--headed", action="store_true", help="show the generation browser (chatgpt-web only; default headless)")
    ap.add_argument("--cdp-url", help="explicit loopback endpoint of an already running, authorized browser/app (chatgpt-web only)")
    ap.add_argument("--cdp-context", type=int, help="explicit existing context index when the app exposes more than one")
    args = ap.parse_args()

    backend = args.backend or os.environ.get("DRAW_BACKEND") or ("local" if args.check_resources else "hf")
    backend = "hf" if backend == "api" else backend
    if backend not in ("hf", "stability", "local", "sdcpp", "chatgpt", "codex", "chatgpt-web"):
        ap.error("DRAW_BACKEND must be hf, api, stability, local, sdcpp, chatgpt, codex or chatgpt-web")
    if backend == "chatgpt":
        sys.stderr.write(
            "draw: 'chatgpt' is a deprecated alias of 'codex'; it uses the Codex allowance, "
            "not a separate ChatGPT Images API. Use --backend codex (or DRAW_BACKEND=codex).\n"
        )
        backend = "codex"
    codex_backend = backend == "codex"
    local_backend = backend in ("local", "sdcpp")
    browser_backend = backend == "chatgpt-web"
    if not browser_backend and any(_option_present(raw_args, flag) for flag in ("--browser-profile", "--browser-channel", "--headed", "--cdp-url", "--cdp-context")):
        ap.error("browser options require --backend chatgpt-web")

    if args.cdp_context is not None and (not args.cdp_url or args.cdp_context < 0):
        ap.error("--cdp-context requires --cdp-url and a nonnegative index")
    if args.cdp_url and any(_option_present(raw_args, flag) for flag in ("--browser-profile", "--browser-channel", "--headed")):
        ap.error("CDP attaches to the existing app; browser launch controls cannot be combined with --cdp-url")

    explicit_codex_generation_flags = [
        flag for flag in ("--codex-model", "--timeout") if _option_present(raw_args, flag)
    ]
    explicit_codex_flags = [
        flag for flag in ("--codex-bin", "--codex-model") if _option_present(raw_args, flag)
    ]
    hf_only_flags = [
        flag for flag in ("--seed", "--negative-prompt", "--width", "--height", "--steps", "--guidance-scale")
        if _option_present(raw_args, flag)
    ]
    if not codex_backend:
        if args.image or (args.check and not browser_backend) or explicit_codex_flags:
            detail = ", ".join(explicit_codex_flags) if explicit_codex_flags else "--image/--check"
            ap.error(f"{detail} require --backend chatgpt (or codex)")
    else:
        if args.model is not None:
            ap.error(
                "--model is HF-only. Codex manages the ChatGPT image model; "
                "omit --model (GPT Image 2.5/Flare/Sunburst cannot be pinned here)."
            )
        if hf_only_flags:
            ap.error(", ".join(hf_only_flags) + " require --backend hf/stability/local/sdcpp")

    if browser_backend:
        if args.model is not None:
            ap.error("--model is not supported by the ChatGPT browser UI; the image model is service-managed")
        if hf_only_flags:
            ap.error(", ".join(hf_only_flags) + " are not supported by chatgpt-web")

    if args.check_resources and not local_backend:
        ap.error("--check-resources requires --backend local or sdcpp")
    if args.json and not args.check_resources:
        ap.error("--json requires --check-resources")
    local_flags = (args.device, args.dtype, args.offload, args.cache_dir, args.revision, args.offline)
    if not local_backend and any(local_flags):
        ap.error("device/precision/offload/cache/revision/offline flags require a local backend")
    native_flags = (args.sdcpp_bin, args.quantization, args.sdcpp_memory, args.native_timeout)
    if backend != "sdcpp" and (any(v is not None for v in native_flags) or args.no_flash_attention):
        ap.error("sdcpp options require --backend sdcpp")
    if backend == "sdcpp" and (args.dtype is not None or args.offload is not None):
        ap.error("sdcpp uses --quantization and --sdcpp-memory, not --dtype/--offload")
    if args.native_timeout is not None and args.native_timeout <= 0:
        ap.error("--native-timeout must be positive")
    if backend != "hf" and args.provider is not None:
        ap.error("--provider applies only to --backend hf")
    if backend != "stability" and args.aspect_ratio is not None:
        ap.error("--aspect-ratio applies only to --backend stability; use --width and --height otherwise")
    if backend == "stability" and any(v is not None for v in (
            args.width, args.height, args.steps, args.guidance_scale)):
        ap.error("Stability API mode does not expose --width/--height/--steps/--guidance-scale; "
                 "use --aspect-ratio, or choose hf/local")
    if args.timeout is not None and (local_backend or args.timeout <= 0):
        ap.error("--timeout must be positive and applies only to API/Codex backends")

    if browser_backend:
        if args.check and (args.prompt is not None or args.out or args.image):
            ap.error("--check does not accept a prompt, output path or reference images")
        prompt = args.prompt
        if not args.check:
            if not args.out:
                ap.error("the following arguments are required: -o/--out")
            if not prompt and not sys.stdin.isatty():
                prompt = sys.stdin.read(1024 * 1024 + 1).strip()
            if not prompt:
                ap.error("prompt is required (arg or stdin)")
        from draw_cli.chatgpt_web import run
        return run(mode="check" if args.check else "generate", prompt=prompt, out_path=args.out,
                   profile=args.browser_profile, channel=args.browser_channel,
                   headed=args.headed, timeout=args.timeout or 600, cdp_url=args.cdp_url, cdp_context=args.cdp_context)

    if codex_backend:
        if args.check:
            if args.prompt or args.out or args.image:
                ap.error("--check does not accept a prompt, output path or reference images")
            if explicit_codex_generation_flags:
                ap.error(
                    "--check does not perform generation and therefore does not accept "
                    + ", ".join(explicit_codex_generation_flags)
                )
            from draw_cli.codex import CodexError, inspect_codex
            try:
                installation = inspect_codex(args.codex_bin)
            except (CodexError, OSError) as exc:
                sys.stderr.write(f"draw: {exc}\n")
                return 1
            print(f"draw: {installation.version} ({installation.binary})")
            print("draw: ChatGPT login and native image-generation client support detected.")
            print(
                "draw: no generation performed; model rollout/plan quota not verified. "
                "The image model is managed by Codex, not pinned by draw."
            )
            return 0
        if not args.out:
            ap.error("the following arguments are required: -o/--out")
        prompt = args.prompt
        if not prompt:
            if not sys.stdin.isatty():
                prompt = sys.stdin.read().strip()
            if not prompt:
                ap.error("prompt is required (arg or stdin)")
        from draw_cli.codex import CodexError, generate as generate_codex
        try:
            sys.stderr.write(
                "draw: using ChatGPT subscription via Codex; "
                "image model is Codex-managed, not pinned.\n"
            )
            generate_codex(
                prompt,
                args.out,
                binary=args.codex_bin,
                model=args.codex_model,
                references=args.image,
                timeout=args.timeout or 600,
            )
        except KeyboardInterrupt:
            sys.stderr.write("draw: cancelled; local Codex process stopped.\n")
            return 130
        except (CodexError, OSError) as exc:
            sys.stderr.write(f"draw: {exc}\n")
            return 1
        print(f"draw: saved {args.out} (ChatGPT subscription via Codex)")
        return 0

    model = normalize_model(args.model or (_default_model() if backend == "hf" else SD35_MODEL))
    if backend != "hf" and model != SD35_MODEL:
        ap.error("local/stability modes currently support only --model sd3.5 (SD 3.5 Large)")

    settings = None
    if local_backend:
        width, height = args.width or 1024, args.height or 1024
        if width % 16 or height % 16:
            ap.error("local --width and --height must be divisible by 16")
        offline = args.offline or os.environ.get("HF_HUB_OFFLINE", "").upper() in ("1", "ON", "YES", "TRUE")
        shared = dict(device=args.device or "auto", cache_dir=args.cache_dir,
                      revision=args.revision or "main", offline=offline, width=width, height=height)
        if backend == "sdcpp":
            from draw_cli.sdcpp import CppSettings
            if shared["device"] not in ("auto", "metal", "mps", "cpu"):
                ap.error("sdcpp --device must be auto, metal, mps or cpu")
            settings = CppSettings(**shared, binary=args.sdcpp_bin, quantization=args.quantization or "q4_0",
                                   memory=args.sdcpp_memory or "resident", flash_attention=not args.no_flash_attention,
                                   timeout=args.native_timeout or 3600)
        else:
            import re
            from draw_cli.local import LocalSettings
            device = shared["device"]
            if device not in ("auto", "cpu", "mps", "cuda") and not re.fullmatch(r"cuda:\d+", device):
                ap.error("--device must be auto, cpu, mps, cuda or cuda:N")
            settings = LocalSettings(**shared, dtype=args.dtype or "auto", offload=args.offload or "auto")
    try:
        if args.check_resources:
            import json
            if backend == "sdcpp":
                from draw_cli.sdcpp import format_report, preflight
            else:
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
        if local_backend:
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
