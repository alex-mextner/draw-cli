"""Native backend contracts; no real model downloads, GPU or paid API calls."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import types
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from draw_cli import sdcpp as cpp
from draw_cli.backends import DrawError

HELP = " ".join(("--list-devices", "--backend", "--model", "--clip_l", "--clip_g", "--t5xxl",
                 "--vae-tiling", "--sampling-method", "--cfg-scale", "--steps", "--seed",
                 "--width", "--height", "--prompt", "--negative-prompt", "--output", "--rng",
                 "--diffusion-fa", "--params-backend"))


@pytest.fixture
def hub(monkeypatch, tmp_path):
    module = types.ModuleType("huggingface_hub")
    module.HfApi = Mock()
    module.get_token = Mock(return_value="secret-test-token")
    module.try_to_load_from_cache = Mock(return_value=None)
    module.snapshot_download = Mock()
    constants = types.ModuleType("huggingface_hub.constants")
    constants.HF_HUB_CACHE = str(tmp_path)
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", constants)
    return module


@pytest.fixture
def plan(tmp_path):
    sizes = {"model": 5 * cpp.GIB, "clip_l": cpp.GIB // 4, "clip_g": cpp.GIB, "t5xxl": 3 * cpp.GIB}
    files = [cpp.Weight(role, name, sizes[role]) for role, name in cpp.recipe("q4_0").items()]
    return cpp.CppPlan(str(tmp_path), "a" * 40, "main", "q4_0", files, 0)


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(cpp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cpp.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(cpp.shutil, "which", lambda name: "/usr/local/bin/sd-cli")
    monkeypatch.setattr(cpp, "probe", Mock(side_effect=lambda b, flag: HELP if flag == "--help" else
                                         "CPU\tCPU\nMetal0\tApple M3 Pro\n"))


@pytest.fixture
def ready(monkeypatch, native, plan):
    monkeypatch.setattr(cpp, "plan_download", Mock(return_value=plan))
    monkeypatch.setattr(cpp, "disk_check", Mock(return_value={"free_bytes": 100 * cpp.GIB}))
    monkeypatch.setattr(cpp, "available_memory", Mock(return_value={"available_bytes": 30 * cpp.GIB,
                                                                    "total_bytes": 36 * cpp.GIB}))
    return plan


def test_import_without_heavy_dependencies():
    code = "import sys; import draw_cli.sdcpp; assert not any(x in sys.modules for x in ('torch','diffusers','psutil','huggingface_hub','PIL'))"
    p = subprocess.run([sys.executable, "-S", "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("quant", cpp.QUANTS)
def test_presets_keep_all_three_encoders(quant):
    r = cpp.recipe(quant)
    assert set(r) == {"model", "clip_l", "clip_g", "t5xxl"}
    assert quant.upper() in r["model"] and quant.upper() in r["t5xxl"]
    assert r["clip_l"] == "clip_l.safetensors"


def test_unknown_quant_is_refused():
    with pytest.raises(DrawError):
        cpp.recipe("q1_unknown")


@pytest.mark.parametrize("device", ["auto", "mps", "metal"])
def test_native_metal_selection(native, device):
    info = cpp.engine(cpp.CppSettings(device=device))
    assert info["device"] == "Metal0" and info["unified_memory"]


def test_cpu_is_explicit(native):
    assert cpp.engine(cpp.CppSettings(device="cpu"))["device"] == "CPU"


def test_cpu_only_build_does_not_pass_metal_check(native, monkeypatch):
    monkeypatch.setattr(cpp, "probe", lambda b, flag: HELP if flag == "--help" else "CPU\tCPU\n")
    with pytest.raises(DrawError, match="no Metal"):
        cpp.engine(cpp.CppSettings())


@pytest.mark.parametrize("flag", ["--backend", "--clip_g", "--t5xxl", "--diffusion-fa", "--params-backend"])
def test_incompatible_binaries_fail_before_weights(native, monkeypatch, flag):
    monkeypatch.setattr(cpp, "probe", lambda b, arg: HELP.replace(flag, ""))
    with pytest.raises(DrawError, match="lacks required"):
        cpp.engine(cpp.CppSettings(memory="disk"))


def test_flash_attention_can_be_disabled(native, monkeypatch):
    monkeypatch.setattr(cpp, "probe", lambda b, flag: HELP.replace("--diffusion-fa", "")
                        if flag == "--help" else "Metal0\tApple GPU\n")
    assert cpp.engine(cpp.CppSettings(flash_attention=False))["device"] == "Metal0"


def test_rosetta_is_refused(native, monkeypatch):
    monkeypatch.setattr(cpp.platform, "machine", lambda: "x86_64")
    with pytest.raises(DrawError, match="Rosetta"):
        cpp.engine(cpp.CppSettings())


def test_missing_binary_has_install_hint(monkeypatch):
    monkeypatch.setattr(cpp.shutil, "which", lambda n: None)
    with pytest.raises(DrawError, match="Install a recent Metal"):
        cpp.engine(cpp.CppSettings(binary="/no/such/sd-cli"))


def test_environment_does_not_pass_tokens(monkeypatch):
    for key in ("HF_TOKEN", "STABILITY_API_KEY", "HUGGING_FACE_HUB_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.setenv(key, "a-secret")
        assert key not in cpp.child_environment()
    assert "PATH" in cpp.child_environment()


def test_probe_has_timeout_and_no_shell(monkeypatch):
    run = Mock(return_value=types.SimpleNamespace(returncode=0, stdout=HELP, stderr=""))
    monkeypatch.setattr(cpp.subprocess, "run", run)
    assert HELP in cpp.probe("/native/sd cli", "--help")
    assert run.call_args.args[0] == ["/native/sd cli", "--help"]
    assert run.call_args.kwargs["timeout"] == 15
    assert not run.call_args.kwargs.get("shell", False)


def test_probe_failure_reported(monkeypatch):
    monkeypatch.setattr(cpp.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("sd-cli", 15)))
    with pytest.raises(DrawError, match="cannot inspect"):
        cpp.probe("sd-cli", "--help")


def set_metadata(hub, plan):
    siblings = [types.SimpleNamespace(rfilename=f.path, size=f.size) for f in plan.files]
    siblings.append(types.SimpleNamespace(rfilename="sd3.5_large.safetensors", size=100 * cpp.GIB))
    hub.HfApi.return_value.model_info.return_value = types.SimpleNamespace(sha=plan.revision, siblings=siblings)


def test_exact_metadata_and_cached_accounting(hub, plan, monkeypatch):
    set_metadata(hub, plan)
    monkeypatch.setattr(cpp, "cached_file", lambda f, *a: "cached" if f.role == "clip_l" else None)
    result = cpp.plan_download(cpp.CppSettings())
    assert result.files == plan.files
    assert result.missing_bytes == result.total_bytes - cpp.GIB // 4
    assert result.required_disk_bytes == result.missing_bytes + 2 * cpp.GIB
    assert hub.HfApi.return_value.model_info.call_args.kwargs["files_metadata"]
    hub.snapshot_download.assert_not_called()


@pytest.mark.parametrize("bad", [None, -1, 0, True])
def test_unknown_file_sizes_fail_closed(hub, plan, bad):
    set_metadata(hub, plan)
    hub.HfApi.return_value.model_info.return_value.siblings[0].size = bad
    with pytest.raises(DrawError, match="manifest"):
        cpp.plan_download(cpp.CppSettings())
    hub.snapshot_download.assert_not_called()


def test_missing_encoder_fails_closed(hub, plan):
    set_metadata(hub, plan)
    hub.HfApi.return_value.model_info.return_value.siblings.pop(2)
    with pytest.raises(DrawError, match="manifest"):
        cpp.plan_download(cpp.CppSettings())


def test_mutable_revision_response_rejected(hub, plan):
    set_metadata(hub, plan)
    hub.HfApi.return_value.model_info.return_value.sha = "main"
    with pytest.raises(DrawError, match="immutable"):
        cpp.plan_download(cpp.CppSettings())


def test_complete_cache_requires_no_space(plan, monkeypatch):
    monkeypatch.setattr(cpp.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=0))
    assert cpp.disk_check(plan)["required_bytes"] == 0
    plan.missing_bytes = 1
    with pytest.raises(DrawError, match="requires"):
        cpp.disk_check(plan)


def test_actual_cache_filesystem(plan, tmp_path, monkeypatch):
    plan.cache_dir = str(tmp_path / "new" / "models")
    disk = Mock(return_value=types.SimpleNamespace(free=100 * cpp.GIB))
    monkeypatch.setattr(cpp.shutil, "disk_usage", disk)
    cpp.disk_check(plan)
    disk.assert_called_once_with(tmp_path)


def test_truncated_cache_not_complete(hub, tmp_path):
    f = tmp_path / "weights"
    f.write_bytes(b"123")
    hub.try_to_load_from_cache.return_value = str(f)
    assert cpp.cached_file(cpp.Weight("model", "m.gguf", 3), str(tmp_path), "a" * 40) == str(f)
    assert cpp.cached_file(cpp.Weight("model", "m.gguf", 4), str(tmp_path), "a" * 40) is None


def test_download_selection_pinned_and_offline_manifest(hub, plan, monkeypatch):
    monkeypatch.setattr(cpp, "cached_file", lambda f, *a: "/models/" + f.path)
    cpp.download(plan, cpp.CppSettings())
    kwargs = hub.snapshot_download.call_args.kwargs
    assert kwargs["revision"] == plan.revision
    assert kwargs["allow_patterns"] == [f.path for f in plan.files]
    hub.get_token.reset_mock()
    offline = cpp.plan_download(cpp.CppSettings(cache_dir=plan.cache_dir, offline=True))
    assert offline.total_bytes == plan.total_bytes and offline.missing_bytes == 0
    hub.get_token.assert_not_called()
    hub.HfApi.assert_not_called()


def test_offline_without_manifest_never_queries_hub(hub):
    with pytest.raises(DrawError, match="offline sdcpp manifest"):
        cpp.plan_download(cpp.CppSettings(offline=True))
    hub.HfApi.assert_not_called()
    hub.snapshot_download.assert_not_called()


def test_offline_download_has_no_token_lookup(hub, plan, monkeypatch):
    monkeypatch.setattr(cpp, "cached_file", lambda f, *a: "/models/" + f.path)
    cpp.download(plan, cpp.CppSettings(offline=True))
    hub.get_token.assert_not_called()
    assert hub.snapshot_download.call_args.kwargs["local_files_only"] is True
    assert hub.snapshot_download.call_args.kwargs["token"] is False


def test_incomplete_download_does_not_write_manifest(hub, plan):
    with pytest.raises(DrawError, match="incomplete/truncated"):
        cpp.download(plan, cpp.CppSettings())
    assert not cpp.manifest_path(plan.cache_dir, plan.quantization, plan.requested_revision).exists()


def test_offline_tampered_manifest_rejected(hub, plan, monkeypatch):
    monkeypatch.setattr(cpp, "cached_file", lambda f, *a: "/models/" + f.path)
    cpp.download(plan, cpp.CppSettings())
    path = cpp.manifest_path(plan.cache_dir, plan.quantization, plan.requested_revision)
    data = json.loads(path.read_text())
    data["files"][0]["path"] = "../../not-a-weight"
    path.write_text(json.dumps(data))
    with pytest.raises(DrawError, match="manifest"):
        cpp.plan_download(cpp.CppSettings(cache_dir=plan.cache_dir, offline=True))


def test_memory_counts_unified_pool_once_and_scales_below_1024(plan):
    base = cpp.CppSettings()
    normal = cpp.memory_estimate(plan, base)
    small = cpp.memory_estimate(plan, replace(base, width=512, height=512))
    disk = cpp.memory_estimate(plan, replace(base, memory="disk"))
    assert 18 * cpp.GIB < normal < 22 * cpp.GIB
    assert small < normal and disk < normal
    assert normal - small == 3 * cpp.GIB


def test_preflight_36_gib_machine_no_fixed_32_gib_gate(ready):
    report, _ = cpp.preflight(cpp.CppSettings())
    assert report["ok"], report["errors"]
    assert report["memory"]["available_bytes"] < 32 * cpp.GIB
    assert "gpu_required_bytes" not in report


def test_insufficient_memory_is_blocked(ready, monkeypatch, hub):
    monkeypatch.setattr(cpp, "available_memory", lambda: {"available_bytes": 2 * cpp.GIB, "total_bytes": 8 * cpp.GIB})
    report, _ = cpp.preflight(cpp.CppSettings())
    assert not report["ok"] and "insufficient available memory" in " ".join(report["errors"])
    hub.snapshot_download.assert_not_called()


def test_capability_failure_still_no_weight_download(ready, monkeypatch, hub):
    monkeypatch.setattr(cpp, "engine", Mock(side_effect=DrawError("no Metal")))
    report, _ = cpp.preflight(cpp.CppSettings())
    assert not report["ok"]
    hub.snapshot_download.assert_not_called()


def test_command_has_native_flags_and_zero_values(plan):
    settings = cpp.CppSettings(memory="disk")
    runtime = {"binary": "/path with space/sd-cli", "device": "Metal0"}
    paths = {f.role: "/models/" + f.path for f in plan.files}
    prompt = "hello; $(touch /tmp/not-executed) --backend cpu"
    args = cpp.command(settings, runtime, paths, "/out.png", prompt, seed=0, negative_prompt="", guidance_scale=0)
    assert args[args.index("--prompt") + 1] == prompt
    assert args[args.index("--seed") + 1] == "0"
    assert args[args.index("--cfg-scale") + 1] == "0"
    assert args[args.index("--backend") + 1] == "Metal0"
    assert args[-2:] == ["--params-backend", "disk"]
    assert "--diffusion-fa" in args and "--vae-tiling" in args
    assert "--clip-on-cpu" not in args and "--type" not in args
    assert not any("http" in arg for arg in args)


def test_real_child_process_failure_tail(tmp_path):
    with pytest.raises(DrawError, match="native test failure"):
        cpp.run_process([sys.executable, "-c", "import sys; print('native test failure'); sys.exit(7)"], 5, str(tmp_path))


def test_real_child_process_timeout(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        cpp.run_process([sys.executable, "-c", "import time; time.sleep(20)"], .1, str(tmp_path))


def test_subprocess_does_not_interpret_shell(tmp_path):
    sentinel = tmp_path / "must-not-exist"
    cpp.run_process([sys.executable, "-c", "import sys; print(sys.argv[1])", f"$(touch {sentinel})"], 5, str(tmp_path))
    assert not sentinel.exists()


@pytest.fixture
def pil(monkeypatch):
    module = types.ModuleType("PIL")
    image = MagicMock()
    image.open.return_value.__enter__.return_value.size = (1024, 1024)
    module.Image = image
    monkeypatch.setitem(sys.modules, "PIL", module)
    return image


def test_failed_preflight_does_not_generate(ready, pil, monkeypatch):
    monkeypatch.setattr(cpp, "preflight", lambda s: ({"ok": False, "errors": ["low memory"], "warnings": []}, None))
    download = Mock()
    monkeypatch.setattr(cpp, "download", download)
    with pytest.raises(DrawError, match="preflight failed"):
        cpp.generate_sdcpp("hello", cpp.CppSettings(), scratch_dir=ready.cache_dir)
    download.assert_not_called()


def test_memory_rechecked_after_download(ready, pil, monkeypatch):
    monkeypatch.setattr(cpp, "download", Mock(return_value={f.role: f.path for f in ready.files}))
    monkeypatch.setattr(cpp, "available_memory", Mock(side_effect=[
        {"available_bytes": 30 * cpp.GIB}, {"available_bytes": cpp.GIB}]))
    run = Mock()
    monkeypatch.setattr(cpp, "run_process", run)
    with pytest.raises(DrawError, match="fell below"):
        cpp.generate_sdcpp("hello", cpp.CppSettings(), scratch_dir=ready.cache_dir)
    run.assert_not_called()


def test_realistic_generation_contract(ready, pil, monkeypatch, capsys):
    monkeypatch.setattr(cpp, "download", Mock(return_value={f.role: f.path for f in ready.files}))
    def run(args, timeout, directory):
        assert "--backend" in args and str(Path(directory) / "image.png") in args
        Path(args[args.index("--output") + 1]).write_bytes(b"fake-test-image")
    monkeypatch.setattr(cpp, "run_process", run)
    result = cpp.generate_sdcpp("robot", cpp.CppSettings(), scratch_dir=ready.cache_dir)
    assert result is pil.open.return_value.__enter__.return_value.copy.return_value
    assert "download excluded" in capsys.readouterr().err
    assert not list(Path(ready.cache_dir).glob(".draw-sdcpp-*"))


def test_no_output_never_reports_success(ready, pil, monkeypatch):
    monkeypatch.setattr(cpp, "download", Mock(return_value={f.role: f.path for f in ready.files}))
    monkeypatch.setattr(cpp, "run_process", Mock())
    with pytest.raises(DrawError, match="without the expected"):
        cpp.generate_sdcpp("robot", cpp.CppSettings(), scratch_dir=ready.cache_dir)


# CLI integration is also exercised in the repository's complete CI checkout.
def test_cli_sdcpp_preserves_explicit_options(monkeypatch):
    from draw_cli import cli
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    generation = Mock()
    monkeypatch.setattr(cli, "generate", generation)
    monkeypatch.setattr(sys, "argv", ["draw", "robot", "--backend", "sdcpp", "--device", "metal",
                                     "--quantization", "q5_0", "--sdcpp-memory", "disk", "--seed", "0", "-o", "out.png"])
    assert cli.main() == 0
    options = generation.call_args.kwargs
    assert options["backend"] == "sdcpp" and options["seed"] == 0
    assert options["settings"].quantization == "q5_0" and options["settings"].memory == "disk"


@pytest.mark.parametrize("args", [["--dtype", "float16"], ["--offload", "sequential"],
    ["--device", "cuda:0"], ["--native-timeout", "0"], ["--width", "257"],
    ["--timeout", "10"], ["--provider", "replicate"], ["--aspect-ratio", "1:1"]])
def test_cli_invalid_native_options(monkeypatch, args):
    from draw_cli import cli
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setattr(sys, "argv", ["draw", "robot", "--backend", "sdcpp", "-o", "out.png", *args])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_cli_resource_json_and_environment(monkeypatch, capsys):
    from draw_cli import cli
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setenv("DRAW_BACKEND", "sdcpp")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    check = Mock(return_value=({"ok": True, "errors": [], "warnings": []}, None))
    monkeypatch.setattr(cpp, "preflight", check)
    monkeypatch.setattr(sys, "stdin", Mock(read=Mock(side_effect=AssertionError("stdin must not be read"))))
    monkeypatch.setattr(sys, "argv", ["draw", "--check-resources", "--json"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert check.call_args.args[0].offline


def test_unfused_attention_reserves_additional_workspace(plan):
    base = cpp.CppSettings()
    normal = cpp.memory_estimate(plan, base)
    unfused = cpp.memory_estimate(plan, replace(base, flash_attention=False))
    large = replace(base, width=2048, height=2048)
    assert unfused - normal == 4 * cpp.GIB
    assert cpp.memory_estimate(plan, replace(large, flash_attention=False)) - cpp.memory_estimate(plan, large) == 64 * cpp.GIB
