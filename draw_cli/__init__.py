"""draw — generate an image from a text prompt via Hugging Face, ChatGPT/Codex, or local
Stable Diffusion (hosted APIs, Diffusers, native Metal/GGUF).

The CLI logic lives in draw_cli.cli; the console entry point is draw_cli.cli:main
(see pyproject.toml [project.scripts]). bin/draw is a thin shim for the legacy
install.sh symlink path.
"""

__version__ = "0.5.0"
