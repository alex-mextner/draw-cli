"""Opt-in direct ChatGPT UI backend. No Codex, cookie export, or paid API fallback."""
from __future__ import annotations
import argparse, fcntl, json, os, shutil, signal, subprocess, sys, tempfile
from pathlib import Path

def main(raw_args: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--backend', choices=['chatgpt-web'], default='chatgpt-web')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--cdp-url', default=os.environ.get('DRAW_CDP_URL', 'http://127.0.0.1:9223'))
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('-o', '--out')
    parser.add_argument('prompt', nargs='?')
    args = parser.parse_args(raw_args)
    if not 30 <= args.timeout <= 900:
        parser.error('--timeout must be between 30 and 900 seconds')
    if args.check and (args.out or args.prompt):
        parser.error('--check does not accept a prompt or output')
    if not args.check and not args.out:
        parser.error('--out is required')
    prompt = '' if args.check else (args.prompt or sys.stdin.read(1048577)).strip()
    if not args.check and (not prompt or len(prompt.encode()) > 1048576):
        parser.error('Prompt must be nonempty UTF-8, at most 1 MiB')
    node = shutil.which('node')
    driver = Path(__file__).resolve().parent.parent / 'browser' / 'driver.mjs'
    if not node or not driver.is_file():
        print('draw: browser driver unavailable; install browser/package.json dependencies', file=sys.stderr)
        return 2
    output = Path(args.out).expanduser().absolute() if args.out else None
    if output and (output.exists() or output.is_symlink() or not output.parent.is_dir()):
        parser.error('Use a fresh output in an existing directory')
    lock_dir = Path.home() / '.cache' / 'draw-cli'
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = lock_dir / 'chatgpt-browser.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock, tempfile.TemporaryDirectory(prefix='draw-browser-') as scratch:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('draw: another direct ChatGPT generation is active', file=sys.stderr)
            return 2
        temporary = Path(scratch) / 'download.png'
        payload = dict(endpoint=args.cdp_url, check=args.check, prompt=prompt,
                       output=str(temporary), timeout=args.timeout)
        env = os.environ.copy()
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'HF_TOKEN', 'STABILITY_API_KEY'):
            env.pop(key, None)
        process = subprocess.Popen([node, str(driver)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, start_new_session=True)
        try:
            stdout, stderr = process.communicate(json.dumps(payload).encode(), timeout=args.timeout + 90)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait()
            print('draw: direct browser process stopped; inspect its owned tab before retry', file=sys.stderr)
            return 2
        if process.returncode:
            sys.stderr.buffer.write(stderr[:4000]); sys.stdout.buffer.write(stdout[:4000])
            return process.returncode
        result = json.loads(stdout)
        if args.check:
            print(json.dumps(result)); return 0
        if not result.get('generated') or temporary.is_symlink() or not temporary.is_file() or not 100 < temporary.stat().st_size <= 32*1024*1024:
            raise ValueError('No fresh valid original image was downloaded')
        from PIL import Image
        with Image.open(temporary) as image:
            image.load()
            if min(image.size) < 256 or max(image.size) > 16384:
                raise ValueError('Downloaded image dimensions are invalid')
            png = Path(scratch) / 'validated.png'
            image.save(png, format='PNG')
        # O_EXCL-style link installation never overwrites an existing output.
        local_temp = output.parent / ('.draw-' + os.path.basename(scratch) + '.png')
        try:
            with local_temp.open('xb') as file:
                os.chmod(local_temp, 0o600); file.write(png.read_bytes())
            os.link(local_temp, output)
        finally:
            local_temp.unlink(missing_ok=True)
        result['output'] = str(output)
        metadata = output.with_suffix('.json')
        with metadata.open('x', encoding='utf8') as file:
            os.chmod(metadata, 0o600); json.dump(result, file, indent=2)
        print(json.dumps(result)); return 0
