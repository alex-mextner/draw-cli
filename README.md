# draw-cli

Generate images from text prompts via Hugging Face Inference API.

## Install

```bash
ln -sfn ~/xp/draw-cli/bin/draw ~/.files/bin/draw
```

## Usage

```bash
draw "a cute robot" -o robot.png
draw "a cute robot" --model black-forest-labs/FLUX.1-schnell -o robot.png
echo "a cute robot" | draw -o robot.png
```

## Env

- `HF_TOKEN` — Hugging Face access token (required)
- `HF_MODEL` — default model (default: `black-forest-labs/FLUX.1-schnell`)
