"""No paid API calls, network access, GPUs or model downloads in these tests.

All optional dependencies are stubbed so the existing stdlib+pytest CI contract
continues to work on Python 3.9 and newer.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import io
import json
import os
import subprocess
import sys
import types
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from draw_cli import backends as api
from draw_cli import cli, local


def module(monkeypatch, name, **attrs):
    result = types.ModuleType(name)
    result.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
    for key, value in attrs.items():
        setattr(result, key, value)
    monkeypatch.setitem(sys.modules, name, result)
    return result


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for key in ("DRAW_BACKEND", "HF_MODEL", "HF_TOKEN", "STABILITY_API_KEY", "HF_HUB_OFFLINE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


@pytest.fixture
def hub(monkeypatch, tmp_path):
    result = module(monkeypatch, "huggingface_hub", HfApi=Mock(), get_token=Mock(return_value="test-token"),
                    InferenceClient=Mock(), try_to_load_from_cache=Mock(return_value=None),
                    get_hf_file_metadata=Mock(), hf_hub_url=Mock(return_value="https://example.invalid/config"),
                    snapshot_download=Mock(return_value=str(tmp_path / "snapshot")))
    module(monkeypatch, "huggingface_hub.constants", HF_HUB_CACHE=str(tmp_path / "cache"))
    return result


@pytest.fixture
def pil(monkeypatch):
    image = Mock()
    image.SAVE = {"PNG": True, "JPEG": True, "WEBP": True}
    image.registered_extensions.return_value = {".png": "PNG", ".jpg": "JPEG", ".webp": "WEBP"}
    module(monkeypatch, "PIL", Image=image)
    return image


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["draw", *args])
    return cli.main()


def files():
    result = [local.ModelFile("model_index.json", 10), local.ModelFile("scheduler/scheduler_config.json", 10)]
    for part in local.WEIGHT_COMPONENTS:
        result += [local.ModelFile(part + "/config.json", 10), local.ModelFile(part + "/model.safetensors", 100)]
    for part in ("tokenizer", "tokenizer_2", "tokenizer_3"):
        result.append(local.ModelFile(part + "/tokenizer_config.json", 10))
    return result


def hardware(device="cuda:0", free=24, ram=64, automatic_cpu=False):
    return dict(device=device, gpu_free_bytes=None if device == "cpu" else free * local.GIB,
                gpu_total_bytes=None if device == "cpu" else 48 * local.GIB,
                ram_available_bytes=ram * local.GIB, ram_total_bytes=128 * local.GIB,
                bf16=device.startswith("cuda"), automatic_cpu=automatic_cpu, cpu_count=8)


@pytest.fixture
def plan(tmp_path):
    return local.DownloadPlan(str(tmp_path), "a" * 40, "main", files(), 0)


@pytest.fixture
def ready(monkeypatch, hub, plan):
    monkeypatch.setattr(local.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(local, "detect_hardware", Mock(return_value=hardware()))
    monkeypatch.setattr(local, "plan_download", Mock(return_value=plan))
    monkeypatch.setattr(local, "check_disk", Mock(return_value={"free_bytes": 100 * local.GIB}))
    return plan


@pytest.mark.parametrize("name", ["sd3.5", "sd3.5-large", "stable-diffusion-3.5-large", "SD3.5", api.SD35_MODEL])
def test_model_aliases(name):
    assert api.normalize_model(name) == api.SD35_MODEL


def test_default_flux_and_api_alias_are_preserved(monkeypatch):
    generate = Mock()
    monkeypatch.setattr(cli, "generate", generate)
    assert run(monkeypatch, "a robot", "-o", "out.png", "--backend", "api") == 0
    assert generate.call_args.args == ("a robot", cli.DEFAULT_MODEL, "out.png")
    assert generate.call_args.kwargs["backend"] == "hf"
    assert generate.call_args.kwargs["steps"] is None


def test_local_defaults_and_explicit_zero_values(monkeypatch):
    generate = Mock()
    monkeypatch.setattr(cli, "generate", generate)
    monkeypatch.setenv("HF_MODEL", "another/api-model")
    assert run(monkeypatch, "robot", "--backend", "local", "--seed", "0", "--guidance-scale", "0",
               "-o", "out.png") == 0
    assert generate.call_args.args[1] == api.SD35_MODEL
    options = generate.call_args.kwargs
    assert options["seed"] == options["guidance_scale"] == 0
    assert options["steps"] == 28 and options["settings"].width == 1024


def test_stdin_and_hf_provider(monkeypatch):
    generate = Mock()
    monkeypatch.setattr(cli, "generate", generate)
    monkeypatch.setattr(sys, "stdin", io.StringIO("a robot\n"))
    assert run(monkeypatch, "--model", "sd3.5", "--provider", "replicate", "-o", "out.png") == 0
    assert generate.call_args.args == ("a robot", api.SD35_MODEL, "out.png")
    assert generate.call_args.kwargs["provider"] == "replicate"


@pytest.mark.parametrize("args", [
    ["--steps", "0"], ["--steps", "101"], ["--seed", "-1"], ["--seed", str(2**32)],
    ["--guidance-scale", "nan"], ["--guidance-scale", "inf"], ["--width", "-1"],
    ["--backend", "local", "--width", "257"], ["--backend", "local", "--device", "cuda:-1"],
    ["--backend", "local", "--model", "unknown"], ["--backend", "local", "--provider", "replicate"],
    ["--backend", "stability", "--steps", "28"], ["--backend", "stability", "--width", "1024"],
    ["--backend", "hf", "--device", "cpu"], ["--aspect-ratio", "1:1"],
    ["--backend", "hf", "--check-resources"], ["--json"], ["--timeout", "0"],
    ["--backend", "local", "--timeout", "10"],
])
def test_invalid_flags_fail_before_generation(monkeypatch, args):
    generate = Mock()
    monkeypatch.setattr(cli, "generate", generate)
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, "robot", "-o", "out.png", *args)
    assert exc.value.code == 2
    generate.assert_not_called()


@pytest.mark.parametrize("ok", [True, False])
def test_json_resource_check_needs_neither_prompt_nor_output(monkeypatch, capsys, ok):
    report = {"ok": ok, "errors": [], "warnings": []}
    preflight = Mock(return_value=(report, None))
    monkeypatch.setattr(local, "preflight", preflight)
    monkeypatch.setattr(sys, "stdin", Mock(read=Mock(side_effect=AssertionError("must not read stdin"))))
    assert run(monkeypatch, "--check-resources", "--json") == (0 if ok else 1)
    assert json.loads(capsys.readouterr().out)["ok"] == ok
    preflight.assert_called_once()


def test_help_version_and_import_do_not_import_heavy_dependencies():
    code = """
import sys
from draw_cli import cli
sys.argv = ['draw', '--version']
try:
    cli.main()
except SystemExit as e:
    assert e.code == 0
assert not any(x in sys.modules for x in ('torch', 'diffusers', 'huggingface_hub', 'PIL', 'psutil'))
"""
    result = subprocess.run([sys.executable, "-S", "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_hf_parameters_and_zero_seed(hub):
    image = api.generate_hf("robot", api.SD35_MODEL, provider="replicate", timeout=15,
                            seed=0, steps=28, guidance_scale=3.5, negative_prompt="blur")
    hub.InferenceClient.assert_called_once_with(token="test-token", provider="replicate", timeout=15)
    hub.InferenceClient.return_value.text_to_image.assert_called_once_with(
        "robot", model=api.SD35_MODEL, seed=0, num_inference_steps=28, guidance_scale=3.5, negative_prompt="blur")
    assert image is hub.InferenceClient.return_value.text_to_image.return_value


def test_hf_omits_unspecified_parameters(hub):
    api.generate_hf("robot", cli.DEFAULT_MODEL)
    hub.InferenceClient.return_value.text_to_image.assert_called_once_with("robot", model=cli.DEFAULT_MODEL)


def test_hf_requires_credentials(hub):
    hub.get_token.return_value = None
    with pytest.raises(api.DrawError, match="HF_TOKEN"):
        api.generate_hf("robot", api.SD35_MODEL)
    hub.InferenceClient.assert_not_called()


def test_api_secrets_redacted_and_no_fallback(hub, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "sensitive-token")
    hub.InferenceClient.return_value.text_to_image.side_effect = RuntimeError("error sensitive-token")
    with pytest.raises(api.DrawError) as exc:
        api.generate_hf("robot", api.SD35_MODEL)
    assert "sensitive-token" not in str(exc.value)
    assert "[redacted]" in str(exc.value)
    hub.snapshot_download.assert_not_called()
    assert "hf_abcdefghijklmnopqrstuvwxyz" not in api.safe_error("hf_abcdefghijklmnopqrstuvwxyz")


def test_stability_multipart_request(monkeypatch, pil):
    monkeypatch.setenv("STABILITY_API_KEY", "unit-test-key")
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"image-bytes"
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(api.urllib.request, "build_opener", Mock(return_value=opener))
    result = api.generate_stability("робот", api.SD35_MODEL, aspect_ratio="16:9", seed=0,
                                    negative_prompt="blur", timeout=12)
    request = opener.open.call_args.args[0]
    assert request.full_url == api.STABILITY_URL and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer unit-test-key"
    payload = request.data.decode()
    assert 'name="model"\r\n\r\nsd3.5-large' in payload
    assert 'name="seed"\r\n\r\n0\r\n' in payload
    assert "робот" in payload and "16:9" in payload and "blur" in payload
    assert "unit-test-key" not in payload
    assert opener.open.call_args.kwargs["timeout"] == 12
    assert result is pil.open.return_value


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
def test_stability_http_errors_not_retried(monkeypatch, pil, status):
    monkeypatch.setenv("STABILITY_API_KEY", "unit-test-key")
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError(api.STABILITY_URL, status, "failed", {},
                                                     io.BytesIO(b'{"error":"unit-test-key"}'))
    monkeypatch.setattr(api.urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(api.DrawError) as exc:
        api.generate_stability("robot", api.SD35_MODEL)
    assert f"HTTP {status}" in str(exc.value) and "unit-test-key" not in str(exc.value)
    assert opener.open.call_count == 1


def test_stability_missing_key_and_redirect_guard(pil):
    with pytest.raises(api.DrawError, match="STABILITY_API_KEY"):
        api.generate_stability("robot", api.SD35_MODEL)
    with pytest.raises(api.DrawError, match="redirect refused"):
        api._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid")


@pytest.mark.parametrize("suffix", ["", ".unknown"])
def test_bad_output_fails_before_paid_api(monkeypatch, pil, tmp_path, suffix):
    generate = Mock()
    monkeypatch.setattr(api, "generate_hf", generate)
    with pytest.raises(api.DrawError, match="extension"):
        cli.generate("robot", api.SD35_MODEL, str(tmp_path / ("out" + suffix)))
    generate.assert_not_called()


def test_output_disk_and_directory_checked(monkeypatch, pil, tmp_path):
    with pytest.raises(api.DrawError, match="directory"):
        api.validate_output(str(tmp_path / "absent" / "out.png"))
    monkeypatch.setattr(api.shutil, "disk_usage", lambda path: types.SimpleNamespace(free=0))
    with pytest.raises(api.DrawError, match="free space"):
        api.validate_output(str(tmp_path / "out.png"))


def test_atomic_save_preserves_old_file_on_failure(tmp_path):
    out = tmp_path / "out.png"
    out.write_bytes(b"original")
    image = Mock()
    def fail(name):
        Path(name).write_bytes(b"partial")
        raise OSError("disk full")
    image.save.side_effect = fail
    with pytest.raises(OSError):
        api.save_image(image, str(out))
    assert out.read_bytes() == b"original" and list(tmp_path.iterdir()) == [out]


def test_jpeg_is_converted_and_saved_atomically(tmp_path):
    image = Mock()
    image.convert.return_value.save.side_effect = lambda name: Path(name).write_bytes(b"jpeg")
    api.save_image(image, str(tmp_path / "out.jpg"))
    image.convert.assert_called_once_with("RGB")
    assert (tmp_path / "out.jpg").read_bytes() == b"jpeg"


def test_download_metadata_excludes_duplicate_weights_and_counts_cache(hub, monkeypatch, tmp_path):
    expected = files()
    siblings = [types.SimpleNamespace(rfilename=f.path, size=f.size) for f in expected]
    siblings += [types.SimpleNamespace(rfilename=n, size=100000) for n in (
        "sd3.5_large.safetensors", "text_encoders/t5xxl_fp16.safetensors", "README.md",
        "transformer/diffusion_pytorch_model.fp16.safetensors")]
    hub.HfApi.return_value.model_info.return_value = types.SimpleNamespace(sha="b" * 40, siblings=siblings)
    monkeypatch.setattr(local, "_cached", lambda f, *a: f.path == "model_index.json")
    result = local.plan_download(local.LocalSettings(cache_dir=str(tmp_path)))
    assert result.files == expected and result.missing_bytes == sum(f.size for f in expected) - 10
    assert result.required_disk_bytes == result.missing_bytes + 2 * local.GIB
    kwargs = hub.HfApi.return_value.model_info.call_args.kwargs
    assert kwargs["files_metadata"] is True and kwargs["revision"] == "main"
    hub.get_hf_file_metadata.assert_called_once()
    hub.snapshot_download.assert_not_called()


@pytest.mark.parametrize("bad_size", [None, 0, -1, True])
def test_missing_or_invalid_size_fails_closed(hub, bad_size):
    siblings = [types.SimpleNamespace(rfilename=f.path, size=f.size) for f in files()]
    siblings[0].size = bad_size
    hub.HfApi.return_value.model_info.return_value = types.SimpleNamespace(sha="b" * 40, siblings=siblings)
    with pytest.raises(api.DrawError, match="manifest"):
        local.plan_download(local.LocalSettings())
    hub.snapshot_download.assert_not_called()


def test_gated_model_failure_before_download(hub):
    hub.HfApi.return_value.model_info.return_value = types.SimpleNamespace(
        sha="a" * 40, siblings=[types.SimpleNamespace(rfilename=f.path, size=f.size) for f in files()])
    hub.get_hf_file_metadata.side_effect = RuntimeError("403 Forbidden")
    with pytest.raises(api.DrawError, match="Accept the model agreement"):
        local.plan_download(local.LocalSettings())
    hub.snapshot_download.assert_not_called()


def test_offline_manifest_reuse_without_network(hub, plan, monkeypatch):
    local._save_manifest(plan)
    monkeypatch.setattr(local, "_cached", lambda *args: True)
    result = local.plan_download(local.LocalSettings(cache_dir=plan.cache_dir, offline=True))
    assert result.missing_bytes == result.required_disk_bytes == 0 and result.revision == plan.revision
    hub.HfApi.assert_not_called()
    hub.get_hf_file_metadata.assert_not_called()
    hub.get_token.assert_not_called()


def test_offline_cache_missing_or_truncated_rejected(hub, plan, monkeypatch):
    settings = local.LocalSettings(cache_dir=plan.cache_dir, offline=True)
    with pytest.raises(api.DrawError, match="offline manifest"):
        local.plan_download(settings)
    local._save_manifest(plan)
    monkeypatch.setattr(local, "_cached", lambda *args: False)
    with pytest.raises(api.DrawError, match="incomplete or truncated"):
        local.plan_download(settings)
    hub.HfApi.assert_not_called()
    hub.snapshot_download.assert_not_called()


def test_cached_file_requires_full_size(hub, tmp_path):
    path = tmp_path / "weight"
    path.write_bytes(b"small")
    hub.try_to_load_from_cache.return_value = str(path)
    assert not local._cached(local.ModelFile("transformer/model.safetensors", 100), str(tmp_path), "a" * 40)
    assert local._cached(local.ModelFile("transformer/model.safetensors", 5), str(tmp_path), "a" * 40)


@pytest.mark.parametrize("name", ["../model_index.json", "/model_index.json", "transformer/../../x.safetensors",
                                 "transformer\\x.safetensors", "evil.py", "text_encoders/model.safetensors"])
def test_unsafe_or_unused_paths_excluded(name):
    assert not local._selected_file(name)


def test_disk_checks_selected_filesystem_and_missing_cache_parent(plan, tmp_path, monkeypatch):
    plan.cache_dir = str(tmp_path / "new" / "cache")
    disk = Mock(return_value=types.SimpleNamespace(free=50 * local.GIB))
    monkeypatch.setattr(local.shutil, "disk_usage", disk)
    local.check_disk(plan)
    disk.assert_called_once_with(tmp_path.resolve())


def test_low_disk_refused_and_full_cache_needs_no_download_space(plan, monkeypatch):
    monkeypatch.setattr(local.shutil, "disk_usage", lambda path: types.SimpleNamespace(free=0))
    plan.missing_bytes = 1
    with pytest.raises(api.DrawError, match="cache disk needs"):
        local.check_disk(plan)
    plan.missing_bytes = 0
    assert local.check_disk(plan)["required_bytes"] == 0


def test_cgroup_v2_uses_smallest_ancestor_headroom(tmp_path):
    (tmp_path / "memory.max").write_text(str(12 * local.GIB))
    (tmp_path / "memory.current").write_text(str(4 * local.GIB))
    child = tmp_path / "group"
    child.mkdir()
    (child / "memory.max").write_text(str(6 * local.GIB))
    (child / "memory.current").write_text(str(3 * local.GIB))
    membership = tmp_path / "membership"
    membership.write_text("0::/group\n")
    assert local.cgroup_available(tmp_path, membership) == 3 * local.GIB


def test_cgroup_v1_and_unlimited_values(tmp_path):
    (tmp_path / "memory.max").write_text("max")
    (tmp_path / "memory.current").write_text("0")
    assert local.cgroup_available(tmp_path, tmp_path / "absent") is None
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "memory.limit_in_bytes").write_text("1000")
    (memory / "memory.usage_in_bytes").write_text("900")
    assert local.cgroup_available(tmp_path, tmp_path / "absent") == 100


@pytest.mark.parametrize("free,mode,gpu", [(48, "none", 32), (24, "model", 20), (12, "sequential", 6)])
def test_automatic_offload_profiles(free, mode, gpu):
    profile = local.memory_profile(local.LocalSettings(), hardware(free=free))
    assert profile["offload"] == mode
    assert profile["gpu_required_bytes"] == gpu * local.GIB
    assert profile["ram_required_bytes"] == 32 * local.GIB
    assert profile["dtype"] == "bfloat16"


def test_memory_estimates_scale_for_resolution_and_fp32():
    high = local.memory_profile(local.LocalSettings(width=2048, height=2048), hardware(free=100))
    fp32 = local.memory_profile(local.LocalSettings(dtype="float32"), hardware(free=100))
    assert high["ram_required_bytes"] == 44 * local.GIB
    assert fp32["ram_required_bytes"] == 64 * local.GIB


def test_explicit_offload_is_not_silently_changed():
    result = local.memory_profile(local.LocalSettings(offload="none"), hardware(free=8))
    assert result["offload"] == "none" and result["gpu_required_bytes"] == 32 * local.GIB


@pytest.mark.parametrize("settings,hw", [
    (local.LocalSettings(dtype="bfloat16"), hardware(device="mps")),
    (local.LocalSettings(dtype="float16"), hardware(device="cpu")),
    (local.LocalSettings(offload="model"), hardware(device="mps")),
])
def test_unsupported_precision_and_offload(settings, hw):
    with pytest.raises(api.DrawError):
        local.memory_profile(settings, hw)


@pytest.mark.parametrize("hw,error", [
    (hardware(ram=2), "insufficient available RAM"),
    (hardware(free=1), "insufficient free GPU memory"),
    (hardware(device="cpu", ram=128, automatic_cpu=True), "no supported GPU"),
])
def test_preflight_resource_failures(ready, monkeypatch, hub, hw, error):
    monkeypatch.setattr(local, "detect_hardware", lambda device: hw)
    report, _ = local.preflight(local.LocalSettings())
    assert not report["ok"] and any(error in e for e in report["errors"])
    hub.snapshot_download.assert_not_called()


def test_missing_optional_dependencies_fail_preflight(ready, monkeypatch, hub):
    monkeypatch.setattr(local.importlib.util, "find_spec", lambda name: None if name == "diffusers" else object())
    report, _ = local.preflight(local.LocalSettings())
    assert not report["ok"] and "diffusers" in " ".join(report["errors"])
    hub.snapshot_download.assert_not_called()


def test_explicit_cpu_allowed_only_with_enough_ram(ready, monkeypatch):
    monkeypatch.setattr(local, "detect_hardware", lambda device: hardware(device="cpu", ram=128))
    report, _ = local.preflight(local.LocalSettings(device="cpu"))
    assert report["ok"] and report["execution"]["dtype"] == "float32"
    assert any("very slow" in x for x in report["warnings"])


@pytest.fixture
def torch_stub(monkeypatch):
    cuda = types.SimpleNamespace(is_available=lambda: True, device_count=lambda: 2,
                                 mem_get_info=lambda index: ((8 if index == 0 else 24) * local.GIB, 32 * local.GIB),
                                 device=lambda index: contextlib.nullcontext(), is_bf16_supported=lambda: True,
                                 get_device_name=lambda index: f"GPU-{index}")
    torch = module(monkeypatch, "torch", cuda=cuda, bfloat16="bf16", float16="fp16", float32="fp32",
                   Generator=Mock(), inference_mode=contextlib.nullcontext,
                   backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)),
                   mps=types.SimpleNamespace(recommended_max_memory=lambda: 40 * local.GIB,
                                             driver_allocated_memory=lambda: 4 * local.GIB))
    module(monkeypatch, "psutil", virtual_memory=lambda: types.SimpleNamespace(
        available=64 * local.GIB, total=128 * local.GIB))
    monkeypatch.setattr(local, "cgroup_available", lambda: None)
    return torch


def test_auto_selects_one_gpu_by_free_memory(torch_stub):
    hw = local.detect_hardware("auto")
    assert hw["device"] == "cuda:1" and hw["gpu_free_bytes"] == 24 * local.GIB
    assert hw["gpu_name"] == "GPU-1"


def test_explicit_unavailable_gpu_fails(torch_stub):
    with pytest.raises(api.DrawError, match="unavailable"):
        local.detect_hardware("cuda:5")


def test_mps_uses_shared_memory_budget(torch_stub, monkeypatch):
    torch_stub.backends.mps.is_available = lambda: True
    monkeypatch.setattr(local, "cgroup_available", lambda: 32 * local.GIB)
    hw = local.detect_hardware("mps")
    assert hw["gpu_free_bytes"] == hw["ram_available_bytes"] == 32 * local.GIB
    assert local.memory_profile(local.LocalSettings(), hw)["dtype"] == "float16"


@pytest.fixture
def pipeline(monkeypatch, torch_stub, hub, ready):
    pipe = MagicMock(spec=["vae", "to", "enable_model_cpu_offload", "enable_sequential_cpu_offload", "__call__"])
    factory = Mock()
    factory.from_pretrained.return_value = pipe
    module(monkeypatch, "diffusers", StableDiffusion3Pipeline=factory)
    monkeypatch.setattr(local, "_cached", lambda *args: True)
    return pipe, factory


def test_local_pipeline_pins_snapshot_and_keeps_all_encoders(pipeline, torch_stub, hub, ready):
    pipe, factory = pipeline
    image = local.generate_local("robot", local.LocalSettings(), seed=0, steps=31, guidance_scale=4)
    kwargs = hub.snapshot_download.call_args.kwargs
    assert kwargs["revision"] == ready.revision
    assert kwargs["allow_patterns"] == [f.path for f in ready.files]
    assert kwargs["cache_dir"] == ready.cache_dir and kwargs["local_files_only"] is False
    factory.from_pretrained.assert_called_once_with(str(Path(ready.cache_dir) / "snapshot"),
        torch_dtype="bf16", local_files_only=True, use_safetensors=True, low_cpu_mem_usage=True)
    pipe.enable_model_cpu_offload.assert_called_once_with(gpu_id=0, device="cuda")
    pipe.to.assert_not_called()
    pipe.vae.enable_tiling.assert_called_once()
    pipe.vae.enable_slicing.assert_called_once()
    torch_stub.Generator.assert_called_once_with(device="cpu")
    torch_stub.Generator.return_value.manual_seed.assert_called_once_with(0)
    assert pipe.call_args.kwargs["num_inference_steps"] == 31
    assert image is pipe.return_value.images[0]
    hub.InferenceClient.assert_not_called()


def test_failed_preflight_never_downloads_or_loads(pipeline, hub, monkeypatch):
    pipe, factory = pipeline
    monkeypatch.setattr(local, "preflight", lambda settings: ({"ok": False, "errors": ["low RAM"], "warnings": []}, None))
    with pytest.raises(api.DrawError, match="preflight failed"):
        local.generate_local("robot", local.LocalSettings())
    hub.snapshot_download.assert_not_called()
    factory.from_pretrained.assert_not_called()


def test_incomplete_download_never_loads_pipeline(pipeline, hub, monkeypatch):
    pipe, factory = pipeline
    monkeypatch.setattr(local, "_cached", lambda *args: False)
    with pytest.raises(api.DrawError, match="incomplete/truncated"):
        local.generate_local("robot", local.LocalSettings())
    factory.from_pretrained.assert_not_called()


def test_memory_is_rechecked_after_download(pipeline, monkeypatch):
    pipe, factory = pipeline
    monkeypatch.setattr(local, "detect_hardware", Mock(side_effect=[hardware(), hardware(ram=1)]))
    with pytest.raises(api.DrawError, match="fell below"):
        local.generate_local("robot", local.LocalSettings())
    factory.from_pretrained.assert_not_called()


def test_local_oom_has_actionable_error_without_api_fallback(pipeline, hub):
    pipe, factory = pipeline
    pipe.side_effect = RuntimeError("CUDA out of memory")
    with pytest.raises(api.DrawError, match="No API request"):
        local.generate_local("robot", local.LocalSettings())
    hub.InferenceClient.assert_not_called()


@pytest.mark.parametrize("name", ["model.fp16-00001-of-00002.safetensors",
                                "model.fp16-00002-of-00002.safetensors",
                                "model.safetensors.index.fp16.json"])
def test_sharded_fp16_variants_are_not_downloaded_twice(name):
    assert not local._selected_file("text_encoder_3/" + name)


def test_local_sequential_offload_uses_selected_gpu(pipeline, monkeypatch):
    pipe, factory = pipeline
    monkeypatch.setattr(local, "detect_hardware", Mock(return_value=hardware(device="cuda:1", free=8)))
    local.generate_local("robot", local.LocalSettings())
    pipe.enable_sequential_cpu_offload.assert_called_once_with(gpu_id=1, device="cuda")
    pipe.to.assert_not_called()


def test_local_offline_snapshot_stays_offline(pipeline, hub):
    local.generate_local("robot", local.LocalSettings(offline=True))
    assert hub.snapshot_download.call_args.kwargs["local_files_only"] is True
    hub.HfApi.assert_not_called()
