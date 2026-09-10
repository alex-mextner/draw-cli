"""Lightweight API backends and shared output handling; no ML imports at startup."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SD35_MODEL = "stabilityai/stable-diffusion-3.5-large"
SD35_ALIASES = {"sd3.5", "sd3.5-large", "stable-diffusion-3.5-large"}
STABILITY_URL = "https://api.stability.ai/v2beta/stable-image/generate/sd3"
ASPECT_RATIOS = ("1:1", "16:9", "21:9", "2:3", "3:2", "4:5", "5:4", "9:16", "9:21")


class DrawError(Exception):
    """An actionable error that the CLI can display without a traceback."""


def normalize_model(model: str) -> str:
    return SD35_MODEL if model.lower() in SD35_ALIASES else model


def safe_error(error: object) -> str:
    text = str(error)
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "STABILITY_API_KEY"):
        secret = os.environ.get(name, "")
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"\b(?:hf_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9_-]{10,})\b", "[redacted]", text)
    # API errors can contain an entire HTML page; keep terminal output bounded.
    return text[:1500]


def validate_output(out_path: str, width: int = 1024, height: int = 1024) -> Path:
    from PIL import Image

    path = Path(out_path).expanduser().absolute()
    Image.init()
    fmt = Image.registered_extensions().get(path.suffix.lower())
    if fmt not in Image.SAVE:
        raise DrawError(f"unsupported output extension: {path.suffix or '(none)'}; use .png, .jpg or .webp")
    if not path.parent.is_dir() or path.is_dir():
        raise DrawError(f"output directory must exist and output must be a file: {path}")
    # Check the actual destination, not CWD; do not truncate an existing image.
    if shutil.disk_usage(path.parent).free < max(16 * 1024**2, width * height * 8):
        raise DrawError(f"insufficient free space for the output image in {path.parent}")
    try:
        with tempfile.TemporaryFile(dir=path.parent):
            pass
    except OSError as exc:
        raise DrawError(f"output directory is not writable: {safe_error(exc)}") from None
    return path


def save_image(image, out_path: str) -> None:
    """Atomic replacement preserves any previous image if encoding fails."""
    path = Path(out_path).expanduser().absolute()
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".draw-", suffix=path.suffix)
    os.close(fd)
    try:
        if path.suffix.lower() in (".jpg", ".jpeg"):
            image = image.convert("RGB")
        image.save(name)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def generate_hf(prompt: str, model: str, *, provider: str = "auto", timeout: float = 300,
                seed=None, negative_prompt=None, width=None, height=None,
                steps=None, guidance_scale=None):
    from huggingface_hub import InferenceClient, get_token

    token = get_token()
    if not token:
        raise DrawError("HF_TOKEN is required for the Hugging Face API (or run `hf auth login`)")
    options = dict(seed=seed, negative_prompt=negative_prompt, width=width, height=height,
                   num_inference_steps=steps, guidance_scale=guidance_scale)
    # Keep existing FLUX behavior: never send SD-specific defaults to other models.
    options = {key: value for key, value in options.items() if value is not None}
    try:
        client = InferenceClient(token=token, provider=provider, timeout=timeout)
        return client.text_to_image(prompt, model=model, **options)
    except Exception as exc:
        raise DrawError(
            f"Hugging Face generation failed: {safe_error(exc)}. "
            "Check the token's Inference Providers permission, provider availability and credits. "
            "No alternative provider or local model was started by draw."
        ) from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DrawError("unexpected Stability API redirect refused; credentials were not forwarded")


def generate_stability(prompt: str, model: str, *, aspect_ratio: str = "1:1",
                       seed=None, negative_prompt=None, timeout: float = 300):
    from PIL import Image

    if model != SD35_MODEL:
        raise DrawError("the stability backend currently supports only --model sd3.5")
    token = os.environ.get("STABILITY_API_KEY", "").strip()
    if not token:
        raise DrawError("STABILITY_API_KEY is required for --backend stability")
    fields = dict(prompt=prompt, model="sd3.5-large", mode="text-to-image",
                  aspect_ratio=aspect_ratio, output_format="png")
    if seed is not None:
        fields["seed"] = str(seed)
    if negative_prompt is not None:
        fields["negative_prompt"] = negative_prompt
    boundary = "draw-" + uuid.uuid4().hex
    body = b"".join(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
         f'{value}\r\n').encode("utf-8") for key, value in fields.items()
    ) + f"--{boundary}--\r\n".encode("ascii")
    request = urllib.request.Request(STABILITY_URL, data=body, headers={
        "Authorization": f"Bearer {token}", "Accept": "image/*",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }, method="POST")
    try:
        # One request, no automatic retry: a timed-out generation may already be billed.
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(32 * 1024**2 + 1)
            if len(data) > 32 * 1024**2:
                raise DrawError("Stability API response exceeded the 32 MiB image limit")
            image = Image.open(io.BytesIO(data))
            image.load()
            return image
    except urllib.error.HTTPError as exc:
        hints = {401: "check STABILITY_API_KEY", 402: "insufficient credits",
                 403: "request denied; check API policy or account permissions",
                 429: "rate limited; retry later"}
        detail = exc.read(2048).decode("utf-8", errors="replace")
        try:
            detail = json.dumps(json.loads(detail), ensure_ascii=False)
        except ValueError:
            pass
        raise DrawError(f"Stability API HTTP {exc.code}: {hints.get(exc.code, 'request failed')}; "
                        f"{safe_error(detail)}") from None
    except DrawError:
        raise
    except Exception as exc:
        raise DrawError(f"Stability API failed: {safe_error(exc)}; not retried automatically") from None
