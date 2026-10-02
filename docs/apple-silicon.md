# Apple Silicon: native SD 3.5 Large with Metal and GGUF

Research and adapter contract checked on **2026-09-05**. This feature adds
`--backend sdcpp`; it does not turn the existing Diffusers backend into a native
Apple engine. API/FLUX defaults, `--backend local`, and its memory checks remain
unchanged. Choose the new backend explicitly for the GGUF path.

## Engines considered

| Engine | Relevant capabilities | Decision for draw-cli |
|---|---|---|
| [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) | C/C++/ggml, SD 3.5, GGUF quantization, Metal, CLI, explicit parameter residency | Implemented as `--backend sdcpp`. Closest to the llama.cpp-style approach. |
| [Draw Things CLI](https://releases.drawthings.ai/p/draw-things-cli-local-media-generation) | Native Draw Things inference stack, scriptable generation and model management | A separate Mac-oriented candidate to benchmark; **not integrated** into draw-cli in this change. |
| [DiffusionKit](https://github.com/argmaxinc/DiffusionKit) | MLX/Core ML diffusion tooling | Repository archived on March 21, 2026; not selected as a new runtime dependency. |
| [MFLUX](https://github.com/mflux-community/mflux) | Native MLX image-generation ecosystem | Investigated, but exact SD 3.5 Large compatibility was not established from the reviewed documentation; not substituted for the requested model. |

Do not equate native support with benchmarked speed. The upstream
[Metal build notes](https://github.com/leejet/stable-diffusion.cpp/blob/6b3edaaf32cc19e5bb2d819c788bd557eddc8eba/docs/build.md)
still warn about inefficient operations on very large matrices. Its
[performance guide](https://github.com/leejet/stable-diffusion.cpp/blob/6b3edaaf32cc19e5bb2d819c788bd557eddc8eba/docs/performance.md)
also notes that Flash Attention can be slower on non-CUDA backends. This adapter
provides quantized weights and controllable memory use, **not a demonstrated
speedup over Diffusers or Draw Things**. No real Mac inference benchmark was
performed during implementation.

## Install the native engine

Use native arm64 Python/Homebrew, not a Rosetta x86_64 environment. draw does not
install or update external executables automatically. With Xcode Command Line
Tools and Homebrew available, build the upstream revision whose CLI contract was
reviewed for this adapter (compilation on a Mac remains a target-machine check):

```bash
brew install cmake
mkdir -p "$HOME/.local/src"
git clone https://github.com/leejet/stable-diffusion.cpp \
  "$HOME/.local/src/stable-diffusion.cpp"
cd "$HOME/.local/src/stable-diffusion.cpp"
git checkout 6b3edaaf32cc19e5bb2d819c788bd557eddc8eba
git submodule update --init --recursive
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSD_METAL=ON
cmake --build build --config Release -j --target sd-cli
export DRAW_SDCPP_BIN="$HOME/.local/src/stable-diffusion.cpp/build/bin/sd-cli"
"$DRAW_SDCPP_BIN" --help
"$DRAW_SDCPP_BIN" --list-devices
```

For an existing source checkout, inspect its state instead of cloning over it.
A different recent build is acceptable when its help advertises the options the
adapter uses and `--list-devices` reports a Metal device. An older CLI, missing
Metal support or Rosetta Python fails before downloading weights. You can set
`DRAW_SDCPP_BIN` in `~/.config/draw-cli/.env`, put `sd-cli` on PATH, or pass
`--sdcpp-bin /absolute/path/to/sd-cli`. Keep build libraries/resources alongside
the binary; do not assume copying the executable alone is sufficient.

Install the lightweight extra in the same environment as draw:

```bash
# From the draw-cli checkout, inside a virtual environment:
python -m pip install ".[sdcpp]"

# To install the current PR branch with pipx, before it is merged:
pipx install --force \
  "draw-cli[sdcpp] @ git+https://github.com/alex-mextner/draw-cli@feat/sd35-api-local-resources"
```

The extra adds **psutil only** to the existing Hugging Face/Pillow dependencies.
It does not install PyTorch, Diffusers, the native executable, or model weights.
This wrapper supports native Metal on Apple Silicon and explicitly requested
CPU; other accelerators remain available through the existing Diffusers backend.

## Check, then generate

```bash
# No model weights downloaded; exact selected-file metadata is read online.
draw --backend sdcpp --quantization q4_0 --check-resources --json

# Custom cache filesystem and output destination checked before generation.
draw --backend sdcpp --quantization q4_0 \
  --cache-dir /Volumes/Models/huggingface --check-resources -o robot.png

# Downloads missing selected files, then runs the native engine locally.
draw "a tiny robot tending a rooftop garden" --backend sdcpp \
  --quantization q4_0 --device metal --seed 42 -o robot.png

# Higher precision preset; quantizes both the main model and T5 accordingly.
draw "a tiny robot" --backend sdcpp --quantization q8_0 --seed 42 -o robot-q8.png

# Explicit lower-residency mode: repeated disk reads can make this slower.
draw "a tiny robot" --backend sdcpp --sdcpp-memory disk \
  --width 768 --height 768 --seed 42 -o robot-low-memory.png
```

Defaults: Q4_0, Metal auto-selection, resident parameters, 1024x1024, 28 steps,
CFG 3.5, Euler sampling, CPU random-number generation, tiled VAE decode and
`--diffusion-fa`. `--device mps` is accepted here as a **Metal alias**; it does not
import PyTorch. CPU requires `--device cpu`, with no implicit CPU fallback.
Dimensions must be multiples of 16 between 256 and 2048. `--seed 0` is preserved.
`--dtype` and `--offload` belong to Diffusers and are rejected by this backend;
use `--quantization` and `--sdcpp-memory` instead.

`--no-flash-attention` allows comparison when enough memory is available. The
preflight reserves additional attention workspace in that mode. A lower-memory
configuration is not necessarily a faster one. `--native-timeout` bounds the
native process, including model loading (3600 seconds by default). Ctrl-C or a
timeout terminates the child process group on macOS. `--timeout` remains API-only.

## Model provenance and download accounting

The presets use the community conversion
[second-state/stable-diffusion-3.5-large-GGUF](https://huggingface.co/second-state/stable-diffusion-3.5-large-GGUF),
not an official Stability AI quantization. The model card identifies
`stabilityai/stable-diffusion-3.5-large` as its base and stable-diffusion.cpp as the
quantizer. Review the model card and applicable license/access conditions before
use; draw does not accept agreements for you.

Exactly four files are selected per preset: `sd3.5_large-Q*_0.gguf` (the full-model
checkpoint, including VAE), `t5xxl-Q*_0.gguf`, `clip_l.safetensors` and
`clip_g.safetensors`. Neither T5 nor either CLIP encoder is omitted. Quantization
can change image quality and prompt interpretation; identical seeds are not
cross-engine or cross-precision equivalence guarantees. No implicit conversion
or requantization is done at generation time.

The Q4_0 selection is approximately **9.5 GB of files**, using the publisher's
rounded decimal sizes, not 9.5 GB of runtime RAM. The full model repository is
much larger because it contains other presets and duplicate full-precision
weights; draw does not download the whole repository. The actual preflight uses
exact Hub metadata sizes, an immutable revision, complete cached-file sizes and
a reserve of the larger of 2 GiB or 5% of missing bytes. Unknown file sizes,
missing companions and incomplete offline snapshots fail closed. The cache
filesystem and output directory are checked separately. Concurrent processes and
external transfer caches can consume space after the check; it is not a disk
reservation or a cryptographic audit of pre-existing cached files.

## Unified-memory planning, not a fixed 32 GiB gate

The sdcpp check counts available Apple unified memory **once**, rather than adding
RAM and a separate VRAM pool. It estimates resident weights from the selected
file sizes, adds 25% allocation overhead, resolution-dependent workspace, and
2 GiB of system headroom. Reducing dimensions below 1024 really lowers the
estimate. There is no fixed 32 GiB minimum in this backend.

Disk residency uses upstream `--params-backend disk`: parameters are reloaded on
demand rather than kept resident for the whole run. Its estimate uses the larger
of the main-model and combined-text-encoder stages, plus the same overhead and
workspace. This is a conservative **planning heuristic**, not a measured peak.
The operating system's file cache, Metal allocations and driver overhead can
still cause memory pressure. The native Metal allocation limit is **not measured**
by this check, and no system/GPU memory limits or environment safety thresholds
are changed. Available memory is checked again after downloads, before launch.
The old Diffusers MPS preflight remains unchanged and is not bypassed.

A passing check does not promise a particular seconds-per-image figure, absence
of swap, or absence of OOM. Under low memory, free resources or explicitly select
another preset, resolution, residency mode, or API backend. No local error
triggers a paid API call. Native processes receive local paths, not HF/API keys,
and are launched without shell interpolation. The expected image must exist,
decode successfully and have the requested dimensions before the final output
is atomically replaced.

## Offline and repeatable comparisons

After a successful draw download, reuse the same backend, preset, cache and
requested revision. `--revision` refers to the GGUF repository for sdcpp, not to
the original Diffusers repository. The resolved commit is shown in the JSON
report and stored in the manifest.

```bash
draw --backend sdcpp --quantization q4_0 --offline --check-resources --json
draw "a tiny robot" --backend sdcpp --quantization q4_0 --offline \
  --seed 42 --steps 28 --width 1024 --height 1024 -o robot.png
```

`HF_HUB_OFFLINE=1` also works. Missing or mismatched manifests/files cause an error,
not a network download. The native executable is still inspected locally.

For meaningful measurements on a Mac, record the chip, GPU cores, RAM, macOS,
engine commit, weights commit/preset, dimensions, steps and CFG. First populate
the cache, then compare matched prompts/settings and inspect image quality:

```bash
/usr/bin/time -l draw "a tiny robot" --backend sdcpp --offline \
  --quantization q4_0 --seed 42 -o benchmark-fa.png
/usr/bin/time -l draw "a tiny robot" --backend sdcpp --offline \
  --quantization q4_0 --no-flash-attention --seed 42 -o benchmark-no-fa.png
```

Each draw invocation starts a fresh native process and reloads the pipeline;
there is no persistent model server in this implementation. The stderr timing
reports native loading plus generation, excluding the preceding download, not
steady-state denoising alone. External timing/RSS tools may not capture all GPU
allocations; also inspect system memory pressure. Do not extrapolate one chip or
another model's published benchmark to SD 3.5 Large.

## Draw Things CLI as a separate native alternative

Draw Things released its standalone CLI on March 25, 2026. The current
[Homebrew Core formula](https://formulae.brew.sh/formula/draw-things-cli) installs
it without the older custom tap:

```bash
brew install draw-things-cli
draw-things-cli --version
draw-things-cli generate --help
draw-things-cli models list
```

Select the exact SD 3.5 Large file ID from the installed model list; do not guess
an ID or silently choose Medium/Turbo. Then use `models ensure --model FILE_ID`
for preparation and `generate --model FILE_ID --prompt "..." --seed 42
--disable-preview --output /absolute/path/result.png` for a local run. Inspect
its installed help because available models and options evolve. Local operation
is the default; no cloud/remote flags are required. These are instructions for
using Draw Things directly, not a claim that `draw --backend drawthings` exists.
The current adapter remains sdcpp, and choosing the fastest engine for a specific
Mac requires matched measurements rather than marketing speed claims.

## Verification boundary

`tests/test_sdcpp.py` checks metadata/cache accounting, unified-memory estimates,
Metal capability discovery, CLI routing, command construction, offline behavior,
error handling, and real small child-process timeout/shell-safety behavior. Model
inference, Metal device responses, downloads and image decoding contracts are
stubbed in these tests. The suite does not download weights, call paid APIs,
compile sd-cli on a Mac, or establish actual image quality/performance.
