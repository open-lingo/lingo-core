"""Generate Nanami TTS for arbitrary multi-character Japanese phrases.

For full Japanese words the trailing-period trick isn't needed —
edge-tts handles multi-mora utterances cleanly. We still pad 300 ms of
trailing silence so any decoder tail-clip lands in silence.

Usage:
    .venv-tts/bin/python -m scripts.tts.gen_phrases おちゃ きょう しゃしん
    .venv-tts/bin/python -m scripts.tts.gen_phrases --from-file /tmp/phrases.txt
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
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


async def synth(text: str, out: Path, attempts: int = 5) -> None:
    import edge_tts  # noqa: PLC0415
    last: Exception | None = None
    for i in range(attempts):
        try:
            comm = edge_tts.Communicate(text, voice=VOICE)
            await comm.save(str(out))
            return
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(2 ** i)
    raise last  # type: ignore[misc]


def pad(src: Path, dst: Path) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-af", f"apad=pad_dur={TRAILING_SILENCE}",
         "-c:a", "libmp3lame", "-q:a", "4", str(dst)],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phrases", nargs="*", help="Phrases to synthesize")
    ap.add_argument("--from-file", help="Read phrases from a file, one per line")
    ap.add_argument("--skip-existing", action="store_true", default=True)
    args = ap.parse_args()

    phrases: list[str] = list(args.phrases)
    if args.from_file:
        phrases.extend(
            l.strip() for l in Path(args.from_file).read_text().splitlines() if l.strip()
        )
    if not phrases:
        print("No phrases provided.", file=sys.stderr)
        return 1
    phrases = list(dict.fromkeys(phrases))  # dedupe, keep order

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    seen: set[str] = set()
    if MARKER.exists():
        seen = {l.strip() for l in MARKER.read_text().splitlines() if l.strip()}

    print(f"Generating {len(phrases)} phrase(s) via Nanami")
    written, skipped = 0, 0
    for phrase in phrases:
        h = primary_hash(phrase)
        out = TTS_DIR / "ja" / f"{h}.mp3"
        rel = f"ja/{h}.mp3"
        manifest_path = f"tts/ja/{h}.mp3"
        key = f"ja:{phrase}"

        if args.skip_existing and key in manifest and out.exists():
            skipped += 1
            print(f"  = {phrase!r}  (already in manifest)")
            manifest[key] = [manifest_path]
            continue

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tmp = Path(tf.name)
        try:
            asyncio.run(synth(phrase, tmp))
            if pad(tmp, out):
                written += 1
                seen.add(rel)
                manifest[key] = [manifest_path]
                print(f"  + {phrase!r}")
            else:
                print(f"  ✗ {phrase!r}  pad failed", file=sys.stderr)
        finally:
            if tmp.exists():
                tmp.unlink()

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    MARKER.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")
    print(f"\nDone. wrote={written} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
