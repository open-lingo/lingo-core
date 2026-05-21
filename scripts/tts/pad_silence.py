"""Pad every TTS mp3 with trailing silence to prevent end-clipping.

Why: edge-tts occasionally finishes an MP3 with a not-quite-complete final
frame. Decoders (notably Chromium's HTMLAudioElement) sometimes truncate
the tail or emit a "click" while flushing — short utterances like "い"
end up sounding like "eek" instead of "ee". Padding 300ms of silence to
the end pushes that artifact into silence and gives the decoder room to
finish cleanly.

Idempotent: tracks padded files in a sidecar `.padded` marker so reruns
are a no-op. Re-pad by deleting the marker (or rerun with --force).

Usage:
    .venv-tts/bin/python -m scripts.tts.pad_silence
    .venv-tts/bin/python -m scripts.tts.pad_silence --pad-ms 500
    .venv-tts/bin/python -m scripts.tts.pad_silence --force
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
TTS_DIR = LINGO_FE / "src" / "pub" / "tts"
MARKER = TTS_DIR / ".padded"


def load_marker() -> set[str]:
    if not MARKER.exists():
        return set()
    return {line.strip() for line in MARKER.read_text().splitlines() if line.strip()}


def save_marker(seen: set[str]) -> None:
    MARKER.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")


def pad_file(src: Path, pad_ms: int) -> bool:
    """Re-encode `src` with trailing silence. Replaces in place. True on success."""
    pad_dur = pad_ms / 1000.0
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(src),
                "-af", f"apad=pad_dur={pad_dur}",
                "-c:a", "libmp3lame",
                "-q:a", "4",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  FAILED {src.name}: {result.stderr.strip()}", file=sys.stderr)
            return False
        shutil.move(str(tmp_path), str(src))
        return True
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pad-ms", type=int, default=300)
    p.add_argument("--force", action="store_true")
    p.add_argument("--lang", default=None, help="Only pad files under this lang subdir")
    args = p.parse_args()

    if not TTS_DIR.exists():
        print(f"No TTS dir at {TTS_DIR}", file=sys.stderr)
        return 1

    seen = set() if args.force else load_marker()
    glob_root = TTS_DIR / args.lang if args.lang else TTS_DIR
    files = sorted(glob_root.rglob("*.mp3"))
    if not files:
        print(f"No mp3s found under {glob_root}")
        return 0

    print(f"Padding {len(files)} mp3 files (+{args.pad_ms}ms silence)")
    if not args.force:
        print(f"  skip-marker: {MARKER.relative_to(REPO_ROOT)} ({len(seen)} cached)")

    padded, skipped, failed = 0, 0, 0
    for f in files:
        rel = str(f.relative_to(TTS_DIR))
        if rel in seen:
            skipped += 1
            continue
        if pad_file(f, args.pad_ms):
            seen.add(rel)
            padded += 1
            if padded % 50 == 0:
                print(f"  ...{padded} padded")
        else:
            failed += 1

    save_marker(seen)
    print(f"\nDone. padded={padded} cached_skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
