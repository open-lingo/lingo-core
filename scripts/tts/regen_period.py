"""Regenerate single-kana Nanami audio using the trailing-period approach.

Why: Whisper audit (scripts/tts/whisper_audit.py) confirmed the current
carrier+trim files are 62% doubled-mora artifacts. The simple
`{kana}。` (trailing kuten) approach is ~80% Whisper-exact for Nanami
single mora — one edge-tts call, no carrier, no trim, no post-process
beyond the trailing-silence pad.

Steps per kana:
1. Synthesize `f"{kana}。"` via edge-tts Nanami
2. Append 300ms of silence (push any decoder tail-clip into silence)
3. Write to the same primary hash path so the manifest is untouched.

Then narrows the manifest so each single-kana entry only emits the
Nanami path (drops Keita from rotation — see whisper data: Keita
can't articulate single mora reliably).

Usage:
    .venv-tts/bin/python -m scripts.tts.regen_period
    .venv-tts/bin/python -m scripts.tts.regen_period --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
TTS_DIR = LINGO_FE / "src" / "pub" / "tts"
MANIFEST = TTS_DIR / "manifest.json"
MARKER = TTS_DIR / ".padded"

VOICE = "ja-JP-NanamiNeural"
TRAILING_SILENCE = 0.30


def primary_hash(text: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}".encode("utf-8")).hexdigest()[:16]


async def synth(text: str, out: Path) -> None:
    import edge_tts  # noqa: PLC0415
    comm = edge_tts.Communicate(text, voice=VOICE)
    await comm.save(str(out))


def pad_and_write(src: Path, dst: Path) -> bool:
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-af", f"apad=pad_dur={TRAILING_SILENCE}",
         "-c:a", "libmp3lame", "-q:a", "4", str(dst)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  FAILED {dst.name}: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets: list[str] = []
    for key in sorted(manifest):
        if not key.startswith("ja:"):
            continue
        text = key[3:]
        if len(text) != 1:
            continue
        targets.append(text)

    print(f"Regenerating {len(targets)} single-kana entries (Nanami, trailing-period approach)")

    seen: set[str] = set()
    if MARKER.exists():
        seen = {line.strip() for line in MARKER.read_text().splitlines() if line.strip()}

    done, failed = 0, 0
    for kana in targets:
        h = primary_hash(kana)
        out = TTS_DIR / "ja" / f"{h}.mp3"
        rel = f"ja/{h}.mp3"

        if args.dry_run:
            print(f"  [dry] {kana} → {out.name}")
            done += 1
            continue

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tmp = Path(tf.name)
        try:
            asyncio.run(synth(f"{kana}。", tmp))
            if pad_and_write(tmp, out):
                seen.add(rel)
                done += 1
                if done % 10 == 0:
                    print(f"  ...{done} done")
            else:
                failed += 1
        finally:
            if tmp.exists():
                tmp.unlink()

    # Narrow manifest: drop the Keita alt path from every single-kana entry.
    narrowed = 0
    for kana in targets:
        key = f"ja:{kana}"
        nanami_path = f"tts/ja/{primary_hash(kana)}.mp3"
        if manifest.get(key) != [nanami_path]:
            manifest[key] = [nanami_path]
            narrowed += 1

    if not args.dry_run:
        MANIFEST.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        MARKER.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")

    print(f"\nDone. wrote={done} failed={failed} manifest-narrowed={narrowed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
