"""Opt-in SD3.5 Large GGUF inference through an external stable-diffusion.cpp.

No torch import, shell, automatic engine installation, cloud fallback, or model
weight download during diagnostics. Memory figures are planning estimates.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from draw_cli.backends import DrawError, SD35_MODEL, safe_error

GIB = 1024**3
REPO = "second-state/stable-diffusion-3.5-large-GGUF"
QUANTS = ("q4_0", "q5_0", "q8_0")
INSTALL = "Install a recent Metal build of stable-diffusion.cpp; see docs/apple-silicon.md."


@dataclass(frozen=True)
class CppSettings:
    binary: str | None = None
    quantization: str = "q4_0"
    device: str = "auto"
    memory: str = "resident"
    cache_dir: str | None = None
    revision: str = "main"
    offline: bool = False
    width: int = 1024
    height: int = 1024
    flash_attention: bool = True
    timeout: float = 3600


@dataclass(frozen=True)
class Weight:
    role: str
    path: str
    size: int


@dataclass
class CppPlan:
    cache_dir: str
    revision: str
    requested_revision: str
    quantization: str
    files: list[Weight]
    missing_bytes: int

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def required_disk_bytes(self) -> int:
        return (self.missing_bytes + max(2 * GIB, math.ceil(self.missing_bytes * .05))
                if self.missing_bytes else 0)


def recipe(quantization: str) -> dict[str, str]:
    if quantization not in QUANTS:
        raise DrawError("supported GGUF presets: " + ", ".join(QUANTS))
    # This full-model GGUF includes the VAE. Keep both CLIPs; do not drop T5.
    quant = quantization.upper()
    return {"model": f"sd3.5_large-{quant}.gguf", "clip_l": "clip_l.safetensors",
            "clip_g": "clip_g.safetensors", "t5xxl": f"t5xxl-{quant}.gguf"}


def child_environment() -> dict[str, str]:
    # The native process only reads local weights. Do not give it API credentials.
    return {k: v for k, v in os.environ.items() if k not in (
        "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "STABILITY_API_KEY", "OPENAI_API_KEY")}


def probe(binary: str, *args: str) -> str:
    try:
        result = subprocess.run([binary, *args], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, errors="replace",
                                timeout=15, check=False, env=child_environment())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DrawError(f"cannot inspect sd-cli: {safe_error(exc)}. {INSTALL}") from None
    if result.returncode:
        raise DrawError(f"sd-cli probe failed ({result.returncode}): "
                        f"{safe_error(result.stderr or result.stdout)}. {INSTALL}")
    return result.stdout + "\n" + result.stderr


def engine(settings: CppSettings) -> dict:
    requested = settings.binary or os.environ.get("DRAW_SDCPP_BIN") or "sd-cli"
    binary = shutil.which(os.path.expanduser(requested))
    if not binary:
        raise DrawError(f"sd-cli executable not found: {requested}. {INSTALL}")
    binary = str(Path(binary).resolve())
    help_text = probe(binary, "--help")
    required = ["--list-devices", "--backend", "--model", "--clip_l", "--clip_g", "--t5xxl",
                "--vae-tiling", "--sampling-method", "--cfg-scale", "--steps", "--seed",
                "--width", "--height", "--prompt", "--negative-prompt", "--output", "--rng"]
    if settings.flash_attention:
        required.append("--diffusion-fa")
    if settings.memory == "disk":
        required.append("--params-backend")
    available_flags = set(re.findall(r"--[a-zA-Z][a-zA-Z0-9_-]*", help_text))
    missing = sorted(set(required) - available_flags)
    if missing:
        raise DrawError(f"sd-cli lacks required options: {', '.join(missing)}. {INSTALL}")
    # Upstream contract: one name<TAB>description per line, not log-message matching.
    devices = {}
    for line in probe(binary, "--list-devices").splitlines():
        name, sep, description = line.partition("\t")
        if sep and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name.strip()):
            devices[name.strip()] = description.strip()
    apple = platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64")
    device = settings.device
    if device in ("auto", "metal", "mps"):
        if not apple:
            raise DrawError("sdcpp auto/metal mode requires native Apple Silicon Python; "
                            "do not run it under Rosetta. CPU requires explicit --device cpu.")
        device = next((name for name in devices if name.lower().startswith("metal")), "")
        if not device:
            raise DrawError("sd-cli reports no Metal device; a CPU-only build is not sufficient. " + INSTALL)
    elif device == "cpu":
        device = next((name for name in devices if name.lower() == "cpu"), "")
        if not device:
            raise DrawError("sd-cli did not report a CPU backend")
    else:
        raise DrawError("sdcpp --device must be auto, metal, mps (Metal alias), or cpu")
    return {"binary": binary, "device": device, "description": devices[device],
            "unified_memory": apple, "memory": settings.memory,
            "flash_attention": settings.flash_attention}


def manifest_path(cache: str, quant: str, revision: str) -> Path:
    key = hashlib.sha256((REPO + "@" + revision + ":" + quant).encode()).hexdigest()[:24]
    return Path(cache) / "draw-cli" / f"sdcpp-{key}.json"


def cached_file(file: Weight, cache: str, revision: str) -> str | None:
    from huggingface_hub import try_to_load_from_cache
    path = try_to_load_from_cache(REPO, file.path, cache_dir=cache, revision=revision)
    try:
        if isinstance(path, str) and Path(path).is_file() and Path(path).stat().st_size == file.size:
            return path
    except OSError:
        pass
    return None


def plan_download(settings: CppSettings) -> CppPlan:
    from huggingface_hub import HfApi, get_token
    from huggingface_hub.constants import HF_HUB_CACHE
    cache = str(Path(settings.cache_dir or HF_HUB_CACHE).expanduser().resolve())
    expected = recipe(settings.quantization)
    if settings.offline:
        try:
            path = manifest_path(cache, settings.quantization, settings.revision)
            if path.stat().st_size > 65536:
                raise ValueError("manifest too large")
            data = json.loads(path.read_text(encoding="utf-8"))
            if (data["repo"] != REPO or data["requested_revision"] != settings.revision
                    or data["quantization"] != settings.quantization):
                raise ValueError("manifest belongs to a different preset/revision")
            revision = data["revision"]
            files = [Weight(**f) for f in data["files"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DrawError(f"offline sdcpp manifest unavailable/invalid: {safe_error(exc)}; "
                            "run this preset once online with the same cache and revision") from None
    else:
        try:
            info = HfApi().model_info(REPO, revision=settings.revision, files_metadata=True,
                                     token=get_token(), timeout=20)
            revision = info.sha
            sizes = {f.rfilename: f.size for f in info.siblings or []}
            files = [Weight(role, name, sizes.get(name)) for role, name in expected.items()]
        except Exception as exc:
            raise DrawError(f"cannot obtain GGUF sizes/access: {safe_error(exc)}") from None
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40,64}", revision):
        raise DrawError("GGUF metadata has no immutable revision")
    if (len(files) != len(expected) or {f.role: f.path for f in files} != expected
            or any(type(f.size) is not int or f.size <= 0 for f in files)):
        raise DrawError("GGUF manifest is incomplete or has unknown sizes; refusing download")
    missing = sum(f.size for f in files if not cached_file(f, cache, revision))
    if settings.offline and missing:
        raise DrawError("offline GGUF cache is incomplete or truncated; rerun online to resume")
    return CppPlan(cache, revision, settings.revision, settings.quantization, files, missing)


def disk_check(plan: CppPlan) -> dict:
    directory = Path(plan.cache_dir)
    while not directory.exists():
        directory = directory.parent
    if not directory.is_dir():
        raise DrawError(f"cache parent is not a directory: {directory}")
    free = shutil.disk_usage(directory).free
    if free < plan.required_disk_bytes:
        raise DrawError(f"cache requires {plan.required_disk_bytes / GIB:.2f} GiB free, "
                        f"has {free / GIB:.2f} GiB at {directory}; choose --cache-dir")
    if plan.missing_bytes:
        with tempfile.TemporaryFile(dir=directory):
            pass
    return {"free_bytes": free, "required_bytes": plan.required_disk_bytes,
            "filesystem_path": str(directory)}


def memory_estimate(plan: CppPlan, settings: CppSettings) -> int:
    # Quantized file size approximates resident weights, not peak process memory.
    # Include allocator overhead, activation workspace and OS headroom separately.
    sizes = {f.role: f.size for f in plan.files}
    resident = sum(sizes.values())
    if settings.memory == "disk":
        resident = max(sizes["model"], sum(v for k, v in sizes.items() if k != "model"))
    area = settings.width * settings.height / 1024**2
    workspace = (2 + 4 * area) * GIB
    if not settings.flash_attention:
        # Non-fused attention can materialize quadratic attention scores.
        workspace += 4 * area**2 * GIB
    return math.ceil(1.25 * resident + workspace + 2 * GIB)


def available_memory() -> dict:
    try:
        import psutil
    except ImportError:
        raise DrawError('sdcpp diagnostics need psutil: install draw-cli[sdcpp] or '
                        '`pipx inject draw-cli psutil` (no PyTorch required)') from None
    vm = psutil.virtual_memory()
    available = vm.available
    if platform.system() == "Linux":
        from draw_cli.local import cgroup_available
        limit = cgroup_available()
        if limit is not None:
            available = min(available, limit)
    return {"available_bytes": available, "total_bytes": vm.total}


def preflight(settings: CppSettings) -> tuple[dict, CppPlan | None]:
    report = {"backend": "sdcpp", "model": SD35_MODEL, "weights_repo": REPO,
              "quantization": settings.quantization, "ok": False, "errors": [], "warnings": [
                  "GGUF presets quantize the denoiser and T5; results can differ from full precision.",
                  "Memory figures are estimates, not measured peaks or guarantees against OOM."]}
    plan = None
    if settings.memory not in ("resident", "disk"):
        report["errors"].append("sdcpp memory mode must be resident or disk")
    try:
        report["engine"] = engine(settings)
        if report["engine"]["unified_memory"]:
            report["warnings"].append("Apple unified RAM is counted once. The native Metal allocation "
                                        "limit is not measured by this check; no system limits are changed.")
        if settings.device == "cpu":
            report["warnings"].append("CPU inference is explicitly enabled and can be very slow")
    except Exception as exc:
        report["errors"].append(safe_error(exc))
    try:
        report["memory"] = available_memory()
    except Exception as exc:
        report["errors"].append(safe_error(exc))
    try:
        plan = plan_download(settings)
        report["download"] = {"cache_dir": plan.cache_dir, "revision": plan.revision,
                              "total_bytes": plan.total_bytes, "missing_bytes": plan.missing_bytes,
                              "files": [asdict(f) for f in plan.files]}
        report["disk"] = disk_check(plan)
        required = memory_estimate(plan, settings)
        report["required_memory_bytes"] = required
        if "memory" in report and report["memory"]["available_bytes"] < required:
            report["errors"].append(f"insufficient available memory: need an estimated {required / GIB:.2f} GiB; "
                                    "try Q4_0, smaller dimensions or --sdcpp-memory disk")
    except Exception as exc:
        report["errors"].append(safe_error(exc))
    if settings.memory == "disk":
        report["warnings"].append("disk parameter residency trades repeated model reads for lower memory use")
    if settings.flash_attention:
        report["warnings"].append("Flash Attention saves workspace but may be slower on Metal; "
                                    "compare with --no-flash-attention on your Mac")
    report["ok"] = not report["errors"]
    return report, plan


def format_report(report: dict) -> str:
    lines = ["draw: sdcpp resource check: " + ("PASS" if report["ok"] else "FAIL")]
    if "engine" in report:
        e = report["engine"]
        lines.append(f"  Engine: {e['binary']}; device: {e['device']} ({e['description']}); residency: {e['memory']}")
    if "download" in report:
        d = report["download"]
        lines.append(f"  GGUF {report['quantization']}: total {d['total_bytes'] / GIB:.2f} GiB, "
                     f"missing {d['missing_bytes'] / GIB:.2f} GiB; cache: {d['cache_dir']}")
    if "memory" in report:
        lines.append(f"  Available memory: {report['memory']['available_bytes'] / GIB:.2f} GiB; "
                     f"estimated need: {report.get('required_memory_bytes', 0) / GIB:.2f} GiB")
    lines += ["  Warning: " + x for x in report["warnings"]]
    lines += ["  Error: " + x for x in report["errors"]]
    return "\n".join(lines)


def download(plan: CppPlan, settings: CppSettings) -> dict[str, str]:
    from huggingface_hub import get_token, snapshot_download
    disk_check(plan)
    snapshot_download(REPO, revision=plan.revision, cache_dir=plan.cache_dir,
                      allow_patterns=[f.path for f in plan.files],
                      local_files_only=settings.offline, max_workers=2,
                      token=False if settings.offline else get_token())
    paths = {f.role: cached_file(f, plan.cache_dir, plan.revision) for f in plan.files}
    if not all(paths.values()):
        raise DrawError("GGUF snapshot is incomplete/truncated; no inference was started")
    if not settings.offline:
        path = manifest_path(plan.cache_dir, plan.quantization, plan.requested_revision)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"repo": REPO, "revision": plan.revision, "requested_revision": plan.requested_revision,
                "quantization": plan.quantization, "files": [asdict(f) for f in plan.files]}
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".sdcpp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return paths


def command(settings: CppSettings, runtime: dict, paths: dict[str, str], output: str,
            prompt: str, *, seed=None, negative_prompt=None, steps=28, guidance_scale=3.5) -> list[str]:
    args = [runtime["binary"], "--backend", runtime["device"], "--model", paths["model"],
            "--clip_l", paths["clip_l"], "--clip_g", paths["clip_g"], "--t5xxl", paths["t5xxl"],
            "--prompt", prompt, "--output", output, "--width", str(settings.width),
            "--height", str(settings.height), "--steps", str(steps), "--cfg-scale", str(guidance_scale),
            "--sampling-method", "euler", "--rng", "cpu", "--vae-tiling"]
    if seed is not None:
        args += ["--seed", str(seed)]
    if negative_prompt is not None:
        args += ["--negative-prompt", negative_prompt]
    if settings.flash_attention:
        args.append("--diffusion-fa")
    if settings.memory == "disk":
        args += ["--params-backend", "disk"]
    return args


def run_process(args: list[str], timeout: float, log_dir: str) -> None:
    # A bounded tail is shown on error; logs stay on the checked output filesystem,
    # never in stdout (which may be consumed by another tool). No shell interpolation.
    with tempfile.TemporaryFile(dir=log_dir) as log:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   env=child_environment(), start_new_session=os.name == "posix")
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            if process.poll() is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()
            raise
        if code:
            log.seek(0, os.SEEK_END)
            length = log.tell()
            log.seek(max(0, length - 8192))
            detail = log.read().decode("utf-8", errors="replace")
            raise DrawError(f"sd-cli exited {code}: {safe_error(detail)}. "
                            "No API fallback was attempted; try a smaller preset/resolution.")


def generate_sdcpp(prompt: str, settings: CppSettings, *, scratch_dir: str, seed=None,
                   negative_prompt=None, steps=28, guidance_scale=3.5):
    from PIL import Image
    report, plan = preflight(settings)
    print(format_report(report), file=sys.stderr)
    if not report["ok"] or plan is None:
        raise DrawError("sdcpp preflight failed; no weights were downloaded or loaded")
    paths = download(plan, settings)
    if available_memory()["available_bytes"] < report["required_memory_bytes"]:
        raise DrawError("available memory fell below the estimate during download; weights cached, not loaded")
    with tempfile.TemporaryDirectory(dir=scratch_dir, prefix=".draw-sdcpp-") as directory:
        output = str(Path(directory) / "image.png")
        args = command(settings, report["engine"], paths, output, prompt, seed=seed,
                       negative_prompt=negative_prompt, steps=steps, guidance_scale=guidance_scale)
        started = time.monotonic()
        print("draw: running local sd-cli (model loading and generation)...", file=sys.stderr)
        try:
            run_process(args, settings.timeout, directory)
        except subprocess.TimeoutExpired:
            raise DrawError("sd-cli timed out and was terminated; no API fallback") from None
        path = Path(output)
        if not path.is_file() or path.stat().st_size == 0:
            raise DrawError("sd-cli returned success without the expected output image")
        with Image.open(path) as image:
            image.load()
            if image.size != (settings.width, settings.height):
                raise DrawError("sd-cli returned an image with unexpected dimensions")
            result = image.copy()
        print(f"draw: native load+generation completed in {time.monotonic() - started:.2f}s "
              "(download excluded)", file=sys.stderr)
        return result
