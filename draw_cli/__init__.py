"""draw — generate an image from a text prompt via Hugging Face Inference API.

The CLI logic lives in draw_cli.cli; the console entry point is draw_cli.cli:main
(see pyproject.toml [project.scripts]). bin/draw is a thin shim for the legacy
install.sh symlink path.
"""

__version__ = "0.2.1"
