"""SD 3.5 Large via Diffusers, with fail-closed preflight before weight downloads.

Disk sizes come from Hub metadata. RAM/VRAM thresholds are deliberately conservative
estimates for an unquantized, full-three-encoder pipeline, NOT hardware guarantees.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from draw_cli.backends import DrawError, SD35_MODEL, safe_error

GIB = 1024**3
COMPONENTS = ("scheduler", "text_encoder", "text_encoder_2", "text_encoder_3",
              "tokenizer", "tokenizer_2", "tokenizer_3", "transformer", "vae")
WEIGHT_COMPONENTS = ("text_encoder", "text_encoder_2", "text_encoder_3", "transformer", "vae")
LOCAL_INSTALL = ('Install local dependencies in the draw environment: '
                 '`pipx inject draw-cli "draw-cli[local] @ '
                 'git+https://github.com/alex-mextner/draw-cli"` '
                 '(or, from a checkout/venv: `python -m pip install ".[local]"`).')


@dataclass(frozen=True)
class LocalSettings:
    device: str = "auto"
    dtype: str = "auto"
    offload: str = "auto"
    cache_dir: str | None = None
    revision: str = "main"
    offline: bool = False
    width: int = 1024
    height: int = 1024


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int


@dataclass
class DownloadPlan:
    cache_dir: str
    revision: str
    requested_revision: str
    files: list[ModelFile]
    missing_bytes: int

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)

    @property
    def required_disk_bytes(self) -> int:
        # Incomplete downloads are not counted as fully cached. Extra room covers
        # metadata and transfer overhead. Completed cached files need no new copy.
        return self.missing_bytes + max(2 * GIB, math.ceil(self.missing_bytes * .05)) if self.missing_bytes else 0


def _selected_file(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    if name == "model_index.json":
        return True
    return (len(path.parts) == 2 and path.parts[0] in COMPONENTS
            and path.suffix in (".json", ".txt", ".model", ".safetensors")
            and not re.search(r"\.(fp16|bf16|fp32|fp8)[.-]", path.name))


def _validate_files(files: list[ModelFile]) -> None:
    names = {item.path for item in files}
    if len(names) != len(files) or any(not _selected_file(f.path) or type(f.size) is not int
                                      or f.size <= 0 for f in files):
        raise DrawError("invalid model file manifest; refusing an unverified download")
    required = {"model_index.json", "scheduler/scheduler_config.json"}
    required.update(f"{part}/config.json" for part in WEIGHT_COMPONENTS)
    required.update(f"{part}/tokenizer_config.json" for part in ("tokenizer", "tokenizer_2", "tokenizer_3"))
    if not required.issubset(names) or any(
        not any(n.startswith(part + "/") and n.endswith(".safetensors") for n in names)
        for part in WEIGHT_COMPONENTS
    ):
        raise DrawError("model metadata is incomplete: expected all SD3 Diffusers components and weights")


def _manifest_path(cache_dir: str, revision: str) -> Path:
    key = hashlib.sha256((SD35_MODEL + "@" + revision).encode()).hexdigest()[:24]
    return Path(cache_dir) / "draw-cli" / f"sd35-{key}.json"


def _cached(item: ModelFile, cache_dir: str, revision: str) -> bool:
    from huggingface_hub import try_to_load_from_cache

    path = try_to_load_from_cache(SD35_MODEL, item.path, cache_dir=cache_dir, revision=revision)
    try:
        return isinstance(path, str) and Path(path).is_file() and Path(path).stat().st_size == item.size
    except OSError:
        return False


def plan_download(settings: LocalSettings) -> DownloadPlan:
    from huggingface_hub import HfApi, get_hf_file_metadata, get_token, hf_hub_url
    from huggingface_hub.constants import HF_HUB_CACHE

    cache_dir = str(Path(settings.cache_dir or HF_HUB_CACHE).expanduser().resolve())
    if settings.offline:
        try:
            data = json.loads(_manifest_path(cache_dir, settings.revision).read_text(encoding="utf-8"))
            if data["model"] != SD35_MODEL or data["requested_revision"] != settings.revision:
                raise ValueError("model/revision mismatch")
            revision = data["revision"]
            files = [ModelFile(**item) for item in data["files"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DrawError("offline manifest unavailable or invalid; run this model/revision once "
                            f"online with draw and the same --cache-dir: {safe_error(exc)}") from None
    else:
        try:
            info = HfApi().model_info(SD35_MODEL, revision=settings.revision,
                                     files_metadata=True, token=get_token(), timeout=20)
            revision = info.sha
            files = [ModelFile(item.rfilename, item.size) for item in info.siblings or []
                     if _selected_file(item.rfilename)]
        except Exception as exc:
            raise DrawError(f"cannot obtain model sizes/access: {safe_error(exc)}. "
                            "Check HF_TOKEN, network and the model's Hugging Face access agreement.") from None
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40,64}", revision):
        raise DrawError("Hub did not return a valid immutable model revision")
    _validate_files(files)
    missing = sum(f.size for f in files if not _cached(f, cache_dir, revision))
    if settings.offline and missing:
        raise DrawError(f"offline cache is incomplete or truncated: {missing / GIB:.2f} GiB missing; "
                        "rerun online to resume the download")
    if missing and not settings.offline:
        # model_info may be public even for a gated model; verify file access before
        # starting any multi-GB transfer. This HEAD request downloads no weights.
        try:
            get_hf_file_metadata(hf_hub_url(SD35_MODEL, "model_index.json", revision=revision),
                                 token=get_token(), timeout=20)
        except Exception as exc:
            raise DrawError(f"model access denied/unavailable: {safe_error(exc)}. Accept the model "
                            "agreement on Hugging Face and configure HF_TOKEN; draw cannot accept it for you.") from None
    return DownloadPlan(cache_dir, revision, settings.revision, files, missing)


def _existing_directory(path: Path) -> Path:
    path = path.expanduser().resolve()
    while not path.exists():
        path = path.parent
    if not path.is_dir():
        raise DrawError(f"cache path has a non-directory parent: {path}")
    return path


def check_disk(plan: DownloadPlan) -> dict:
    directory = _existing_directory(Path(plan.cache_dir))
    free = shutil.disk_usage(directory).free
    if free < plan.required_disk_bytes:
        raise DrawError(f"cache disk needs {plan.required_disk_bytes / GIB:.2f} GiB free "
                        f"but has {free / GIB:.2f} GiB at {directory}; use --cache-dir on a larger disk")
    if plan.missing_bytes:
        try:
            with tempfile.TemporaryFile(dir=directory):
                pass
        except OSError as exc:
            raise DrawError(f"model cache is not writable: {safe_error(exc)}") from None
    return {"filesystem_path": str(directory), "free_bytes": free,
            "required_bytes": plan.required_disk_bytes}


def cgroup_available(root: Path = Path("/sys/fs/cgroup"),
                     membership: Path = Path("/proc/self/cgroup")) -> int | None:
    """Cap host RAM by effective cgroup v1/v2 headroom; swap is not counted as RAM."""
    candidates = {root, root / "memory"}
    try:
        for line in membership.read_text().splitlines():
            _, controllers, relative = line.split(":", 2)
            if controllers and "memory" not in controllers.split(","):
                continue
            parts = PurePosixPath(relative).parts[1:]
            if ".." in parts:
                continue
            base = root / "memory" if controllers else root
            for count in range(len(parts) + 1):
                candidates.add(base.joinpath(*parts[:count]))
    except (OSError, ValueError):
        pass
    limits = []
    for path in candidates:
        for limit_name, usage_name in (("memory.max", "memory.current"),
                                       ("memory.limit_in_bytes", "memory.usage_in_bytes")):
            try:
                limit = int((path / limit_name).read_text().strip())
                usage = int((path / usage_name).read_text().strip())
                if limit >= 0 and usage >= 0:
                    limits.append(max(0, limit - usage))
            except (OSError, ValueError):
                pass
    return min(limits) if limits else None


def detect_hardware(device: str) -> dict:
    import psutil
    import torch

    vm = psutil.virtual_memory()
    ram = vm.available
    cap = cgroup_available()
    if cap is not None:
        ram = min(ram, cap)
    result = {"ram_available_bytes": ram, "ram_total_bytes": vm.total,
              "cpu_count": os.cpu_count(), "device": device, "gpu_free_bytes": None,
              "gpu_total_bytes": None, "bf16": False, "automatic_cpu": False}
    if device == "auto":
        if torch.cuda.is_available():
            # Select one visible GPU by free memory; never add unrelated GPUs' VRAM.
            index = max(range(torch.cuda.device_count()), key=lambda i: torch.cuda.mem_get_info(i)[0])
            device = f"cuda:{index}"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
            result["automatic_cpu"] = True
    if device == "cuda" or re.fullmatch(r"cuda:\d+", device):
        index = int(device.split(":")[1]) if ":" in device else 0
        if not torch.cuda.is_available() or index >= torch.cuda.device_count():
            raise DrawError(f"requested {device} is unavailable; check your PyTorch build and GPU driver")
        free, total = torch.cuda.mem_get_info(index)
        with torch.cuda.device(index):
            bf16 = torch.cuda.is_bf16_supported()
        result.update(device=f"cuda:{index}", gpu_free_bytes=free, gpu_total_bytes=total,
                      gpu_name=torch.cuda.get_device_name(index), bf16=bf16)
    elif device == "mps":
        if not torch.backends.mps.is_available():
            raise DrawError("MPS is unavailable; use an Apple Silicon-compatible PyTorch build")
        budget = torch.mps.recommended_max_memory() - torch.mps.driver_allocated_memory()
        result.update(device="mps", gpu_name="Apple MPS (unified memory)",
                      gpu_free_bytes=max(0, min(ram, budget)))
    elif device == "cpu":
        result["device"] = "cpu"
    else:
        raise DrawError("--device must be auto, cpu, mps, cuda or cuda:N")
    return result


def memory_profile(settings: LocalSettings, hardware: dict) -> dict:
    device = hardware["device"]
    dtype = settings.dtype
    if dtype == "auto":
        dtype = "float32" if device == "cpu" else ("bfloat16" if hardware["bf16"] else "float16")
    if device == "cpu" and dtype != "float32":
        raise DrawError("CPU mode uses float32; choose --dtype auto or float32")
    if dtype == "bfloat16" and not hardware["bf16"]:
        raise DrawError("bfloat16 is not supported by the selected device; use --dtype auto or float16")
    scale = 2 if dtype == "float32" else 1
    workspace = math.ceil(4 * GIB * max(1, settings.width * settings.height / 1024**2) * scale)
    weights = 28 * GIB * scale
    offload = settings.offload
    if offload == "auto":
        offload = "none"
        if device.startswith("cuda"):
            free = hardware["gpu_free_bytes"]
            offload = "none" if free >= weights + workspace else (
                "model" if free >= 16 * GIB * scale + workspace else "sequential")
    if offload != "none" and not device.startswith("cuda"):
        raise DrawError("model/sequential CPU offload currently requires CUDA; use --offload none")
    gpu_need = {"none": weights, "model": 16 * GIB * scale,
                "sequential": 2 * GIB * scale}[offload] + workspace
    return {"dtype": dtype, "offload": offload, "ram_required_bytes": weights + workspace,
            "gpu_required_bytes": 0 if device == "cpu" else gpu_need}


def preflight(settings: LocalSettings) -> tuple[dict, DownloadPlan | None]:
    report = {"model": SD35_MODEL, "ok": False, "errors": [], "warnings": [
        "RAM/VRAM requirements are conservative estimates, not a guarantee against OOM."
    ]}
    plan = None
    missing_deps = [module for module in ("torch", "diffusers", "transformers", "accelerate", "sentencepiece", "psutil")
                    if importlib.util.find_spec(module) is None]
    if missing_deps:
        report["errors"].append(f"missing dependencies: {', '.join(missing_deps)}. {LOCAL_INSTALL}")
    try:
        hardware = detect_hardware(settings.device)
        report["hardware"] = hardware
        profile = memory_profile(settings, hardware)
        report["execution"] = profile
        if hardware["automatic_cpu"]:
            report["errors"].append("no supported GPU found; CPU inference is very slow. "
                                    "Select --device cpu explicitly to allow it, or choose an API backend.")
        if hardware["ram_available_bytes"] < profile["ram_required_bytes"]:
            report["errors"].append(
                f"insufficient available RAM: {hardware['ram_available_bytes'] / GIB:.2f} GiB; "
                f"estimated requirement {profile['ram_required_bytes'] / GIB:.2f} GiB (swap excluded)")
        if hardware["device"] != "cpu" and hardware["gpu_free_bytes"] < profile["gpu_required_bytes"]:
            report["errors"].append(
                f"insufficient free GPU memory: {hardware['gpu_free_bytes'] / GIB:.2f} GiB; "
                f"estimated requirement {profile['gpu_required_bytes'] / GIB:.2f} GiB; "
                "try --offload sequential, a smaller resolution, or an API backend")
        if profile["offload"] == "sequential":
            report["warnings"].append("sequential offload saves VRAM but can be substantially slower")
        if hardware["device"] == "cpu":
            report["warnings"].append("CPU generation is very slow and uses float32")
    except Exception as exc:
        report["errors"].append(f"cannot validate compute resources: {safe_error(exc)}")
    try:
        plan = plan_download(settings)
        report["download"] = {"cache_dir": plan.cache_dir, "revision": plan.revision,
                              "total_bytes": plan.total_bytes, "missing_bytes": plan.missing_bytes,
                              "cached_bytes": plan.total_bytes - plan.missing_bytes,
                              "required_disk_bytes": plan.required_disk_bytes}
        report["disk"] = check_disk(plan)
    except Exception as exc:
        report["errors"].append(safe_error(exc))
    report["ok"] = not report["errors"]
    return report, plan


def format_report(report: dict) -> str:
    lines = ["draw: SD 3.5 Large resource check: " + ("PASS" if report["ok"] else "FAIL")]
    if "hardware" in report:
        hw = report["hardware"]
        lines.append(f"  Device: {hw['device']}; available RAM: {hw['ram_available_bytes'] / GIB:.2f} GiB")
        if hw["gpu_free_bytes"] is not None:
            lines.append(f"  Free GPU memory: {hw['gpu_free_bytes'] / GIB:.2f} GiB")
    if "execution" in report:
        ex = report["execution"]
        lines.append(f"  Execution: {ex['dtype']}, offload={ex['offload']}; estimated RAM "
                     f"{ex['ram_required_bytes'] / GIB:.2f} GiB, GPU {ex['gpu_required_bytes'] / GIB:.2f} GiB")
    if "download" in report:
        dl = report["download"]
        lines.append(f"  Cache: {dl['cache_dir']}; cached {dl['cached_bytes'] / GIB:.2f} GiB, "
                     f"download {dl['missing_bytes'] / GIB:.2f} GiB, "
                     f"disk needed with reserve {dl['required_disk_bytes'] / GIB:.2f} GiB")
    for message in report["warnings"]:
        lines.append("  Warning: " + message)
    for message in report["errors"]:
        lines.append("  Error: " + message)
    return "\n".join(lines)


def _save_manifest(plan: DownloadPlan) -> None:
    path = _manifest_path(plan.cache_dir, plan.requested_revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"model": SD35_MODEL, "revision": plan.revision,
            "requested_revision": plan.requested_revision, "files": [asdict(f) for f in plan.files]}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".manifest-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def generate_local(prompt: str, settings: LocalSettings, *, seed=None, negative_prompt=None,
                   steps: int = 28, guidance_scale: float = 3.5):
    report, plan = preflight(settings)
    print(format_report(report), file=sys.stderr)
    if not report["ok"] or plan is None:
        raise DrawError("local preflight failed; no model weights were downloaded or loaded")
    try:
        # Import the actual runtime BEFORE downloading; catches broken optional installs.
        import torch
        from diffusers import StableDiffusion3Pipeline
        from huggingface_hub import get_token, snapshot_download

        check_disk(plan)  # refresh free space immediately before transfer
        snapshot = snapshot_download(SD35_MODEL, revision=plan.revision, cache_dir=plan.cache_dir,
                                     token=get_token(), allow_patterns=[f.path for f in plan.files],
                                     local_files_only=settings.offline, max_workers=2)
        if not all(_cached(f, plan.cache_dir, plan.revision) for f in plan.files):
            raise DrawError("downloaded snapshot is incomplete/truncated; rerun to resume before loading")
        try:
            _save_manifest(plan)
        except OSError as exc:
            print(f"draw: warning: cannot save offline manifest: {safe_error(exc)}", file=sys.stderr)
        execution = report["execution"]
        device = report["hardware"]["device"]
        # Transfers can take a long time: check live memory again before loading.
        current = detect_hardware(device)
        if (current["ram_available_bytes"] < execution["ram_required_bytes"] or
                (device != "cpu" and current["gpu_free_bytes"] < execution["gpu_required_bytes"])):
            raise DrawError("available RAM/GPU memory fell below the preflight estimate during download; "
                            "weights are cached but were not loaded. Free resources and retry.")
        pipe = StableDiffusion3Pipeline.from_pretrained(
            snapshot, torch_dtype=getattr(torch, execution["dtype"]),
            local_files_only=True, use_safetensors=True, low_cpu_mem_usage=True,
        )
        pipe.vae.enable_tiling()
        pipe.vae.enable_slicing()
        if execution["offload"] == "none":
            pipe.to(device)
        else:
            index = int(device.split(":")[1])
            if execution["offload"] == "model":
                pipe.enable_model_cpu_offload(gpu_id=index, device="cuda")
            else:
                pipe.enable_sequential_cpu_offload(gpu_id=index, device="cuda")
        generator = torch.Generator(device="cpu").manual_seed(seed) if seed is not None else None
        with torch.inference_mode():
            return pipe(prompt=prompt, negative_prompt=negative_prompt or "",
                        width=settings.width, height=settings.height, num_inference_steps=steps,
                        guidance_scale=guidance_scale, generator=generator).images[0]
    except DrawError:
        raise
    except Exception as exc:
        if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
            raise DrawError("local generation ran out of memory despite preflight; close other GPU/RAM "
                            "users, reduce --width/--height or use --offload sequential. "
                            "No API request was made.") from None
        raise DrawError(f"local generation failed: {safe_error(exc)}. {LOCAL_INSTALL}") from None
