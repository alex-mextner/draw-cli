# Stable Diffusion 3.5 Large

`sd3.5`, `sd3.5-large` and `stable-diffusion-3.5-large` are aliases for
`stabilityai/stable-diffusion-3.5-large`. Local execution and the direct Stability
backend support **Large**, not Medium or Large Turbo. Hugging Face mode still
accepts other model IDs and still defaults to FLUX.1-schnell.

## Hosted inference (no PyTorch installation)

```bash
# Hugging Face Inference Providers. --backend api is an alias for hf.
export HF_TOKEN=your_token
draw "a tiny robot tending a rooftop garden" --model sd3.5 -o robot.png

# Select a provider explicitly; availability depends on HF's current catalogue.
draw "a tiny robot" --model sd3.5 --provider replicate --seed 42 -o robot.png

# Direct Stability AI API; a Hugging Face token is not required.
export STABILITY_API_KEY=your_key
draw "a tiny robot" --backend stability --aspect-ratio 16:9 -o robot.png
```

Keys can also be stored in `~/.config/draw-cli/.env`; existing environment values
win. HF also supports a cached `hf auth login` token. For hosted HF inference,
use a token with Inference Providers permission. Provider availability, quotas,
credits and charges depend on the account/provider; a token is not a promise of
free generation. The direct Stability backend uses its own API key and credits.

HF forwards `--seed`, `--negative-prompt`, `--width`, `--height`, `--steps` and
`--guidance-scale` only when supplied. Supported options are provider-dependent.
Direct Stability uses `--aspect-ratio`, `--seed` and `--negative-prompt` instead;
width/height/steps/guidance flags are rejected, not silently ignored. API timeout
is configurable with `--timeout` (300 seconds by default). Stability treats seed
0 as random; local/HF seed semantics can differ, so seeds are not cross-backend
reproducibility guarantees. draw does not retry direct Stability generations,
follow redirects carrying its key, or silently switch to another backend.

## Local installation

Install the optional extra in the same environment as `draw`:

```bash
# From a checkout, inside a virtual environment:
python -m pip install ".[local]"

# Alternative: new isolated pipx installation with the local extra:
pipx install "draw-cli[local] @ git+https://github.com/alex-mextner/draw-cli"

# Add the extra to an existing pipx installation:
pipx inject draw-cli "draw-cli[local] @ git+https://github.com/alex-mextner/draw-cli"
```

The base installation remains lightweight. The extra adds PyTorch, Diffusers,
Transformers, Accelerate, safetensors, SentencePiece, protobuf and psutil. It does
not install a GPU driver or download model weights. Install a PyTorch build
compatible with your accelerator/driver; CUDA, Apple MPS and explicit CPU mode
are detected at runtime. Python 3.10+ is recommended for the local stack; the base
CLI retains Python 3.9 compatibility.

Accept the conditions on the [model page](https://huggingface.co/stabilityai/stable-diffusion-3.5-large)
yourself and configure `HF_TOKEN` with access to the gated repository, or run
`hf auth login`. draw never accepts a license agreement on your behalf.

```bash
# Diagnostics only: no prompt/output required, no model-weight download.
draw --check-resources

# Check the actual cache and output filesystems; the output parent must exist.
draw --check-resources --cache-dir /mnt/models/huggingface -o robot.png

# Machine-readable report, exit 0 = passed, 1 = blocked.
draw --check-resources --cache-dir /mnt/models/huggingface --json

# Generation automatically repeats preflight before downloading/loading.
draw "a tiny robot tending a rooftop garden" --backend local \
  --cache-dir /mnt/models/huggingface --seed 42 -o robot.png

# Explicit controls:
draw "a tiny robot" --backend local --device cuda:0 --offload sequential \
  --width 768 --height 768 --steps 28 --guidance-scale 3.5 -o robot.png
```

Local defaults: 1024x1024, 28 steps, guidance 3.5, automatic device/dtype/offload.
Dimensions must be multiples of 16 between 256 and 2048. `--dtype` accepts auto,
float16, bfloat16 and float32; incompatible device/dtype combinations fail before
weights download. `--device auto` selects the visible CUDA GPU with the most
**free** memory, otherwise MPS. It does not silently start a very slow CPU run:
CPU requires `--device cpu` and uses float32.

## What is checked

**Disk and model access.** Metadata provides sizes for the exact Diffusers files
at an immutable commit. Downloads exclude duplicate root checkpoints, alternate
text-encoder bundles and fp16 variant shards. Completed files in the same cache
and revision count as cached only when their sizes match. Required new space is
missing bytes plus the larger of 2 GiB or 5% reserve. Incomplete files are counted
conservatively as still missing. Metadata/access failures or missing sizes block
the download. The check uses the actual cache filesystem, including custom
`--cache-dir`, `HF_HUB_CACHE`/`HF_HOME` and resolved symlinks, not the working
directory. The output directory is checked separately for format, space and
writability before any generation call. Existing images are replaced atomically
only after encoding succeeds. Space consumed concurrently by other processes or
external transfer caches cannot be reserved by this check.

**Compute resources.** Preflight checks optional dependencies, available RAM
(excluding swap), cgroup v1/v2 memory headroom, accelerator availability,
current free VRAM on the selected GPU, resolution and dtype. VRAM from separate
GPUs is never added together. MPS unified memory is not counted twice. RAM and
VRAM are checked again after downloading and before pipeline loading.

The memory thresholds below are **draw's conservative planning heuristics**, not
measured minimum requirements or a guarantee against out-of-memory errors. At
1024x1024 with float16/bfloat16 and all three text encoders, the estimates are:

| Execution | Available RAM | Free GPU memory |
|---|---:|---:|
| GPU, no offload | 32 GiB | 32 GiB |
| CUDA model offload | 32 GiB | 20 GiB |
| CUDA sequential offload | 32 GiB | 6 GiB |
| CPU float32 | 64 GiB | Not applicable |

Installed RAM is not available RAM. Higher resolutions and float32 increase the
estimates. Automatic CUDA offload chooses none, model or sequential according to
free memory; sequential offload can be much slower. Explicit settings are never
silently changed. MPS does not support these CUDA CPU-offload modes here. The
implementation retains T5 and both CLIP encoders, uses safetensors and VAE
tiling/slicing, and does not silently quantize weights or reduce the model.

On failure, free resources, select a larger cache disk, reduce resolution, or
explicitly choose an API backend. No local failure triggers a paid API request.

## Offline and pinned revisions

```bash
# Optional: pin a Hub commit instead of the moving main revision.
draw "robot" --backend local --revision <model-commit-sha> -o robot.png

# After one successful download through draw, reuse the same cache/revision.
draw --check-resources --offline --cache-dir /mnt/models/huggingface --json
draw "robot" --backend local --offline --cache-dir /mnt/models/huggingface -o robot.png
```

`--offline` and `HF_HUB_OFFLINE=1` use a local manifest written after a verified
snapshot download. No remote metadata requests are made. Missing manifests,
wrong revisions or truncated/missing files cause an error instead of a download.
A cache populated only by another application needs a one-time online draw run
to create the manifest; completed matching files are reused without downloading
them again. A read-only cache without a manifest cannot support this mode.

## Verification and references

`python -m pytest tests/ -q` includes dependency-free mocked backend/preflight
coverage; it makes no paid calls and downloads no models. Real GPU/MPS/CPU model
inference and paid provider calls require separate integration verification on
the target machine/account. Passing a mocked test is not that verification.

- [Model card and access conditions](https://huggingface.co/stabilityai/stable-diffusion-3.5-large)
- [Diffusers SD3/SD3.5 pipeline and offloading](https://huggingface.co/docs/diffusers/api/pipelines/stable_diffusion/stable_diffusion_3)
- [Hugging Face InferenceClient](https://huggingface.co/docs/huggingface_hub/package_reference/inference_client)
- [Hugging Face cache and downloads](https://huggingface.co/docs/huggingface_hub/package_reference/file_download)
- [Stability AI API reference](https://platform.stability.ai/docs/api-reference)
