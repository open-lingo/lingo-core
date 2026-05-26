"""Generate an alternate TTS voice for every existing manifest entry.

Why a separate script: the main `generate.py` hashes only `(lang, text)`
and stores a single mp3 path per manifest key. To support multiple voices
without overwriting the primary, this script:

  - hashes `(lang, text, voice)` so each voice gets its own filename,
  - rewrites manifest values to **arrays** (`[primary, alt]`) when the
    primary entry is currently a bare string, then appends the new path,
  - skips synthesis when the alt file already exists on disk.

Run after the main generator. The frontend resolver (`shared/japanese/tts.ts`)
already understands the string-or-array shape — picking a random entry for
voice variety on each playback.

Usage:
    .venv-tts/bin/python -m scripts.tts.add_alt_voice
    .venv-tts/bin/python -m scripts.tts.add_alt_voice --voice ja-JP-KeitaNeural
    .venv-tts/bin/python -m scripts.tts.add_alt_voice --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
OUT_DIR = LINGO_FE / "src" / "pub" / "tts"
MANIFEST = OUT_DIR / "manifest.json"


def load_manifest() -> dict[str, object]:
    if not MANIFEST.exists():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, object]) -> None:
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def alt_hash(lang: str, text: str, voice: str) -> str:
    return hashlib.sha256(f"{lang}:{text}::{voice}".encode()).hexdigest()[:16]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--voice", default="ja-JP-KeitaNeural")
    p.add_argument("--lang", default="ja")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    manifest = load_manifest()
    if not manifest:
        print("Empty manifest — run scripts/tts/generate.py first.", file=sys.stderr)
        return 1

    prefix = f"{args.lang}:"
    keys = [k for k in manifest if k.startswith(prefix)]
    if not keys:
        print(f"No {args.lang} entries in manifest.", file=sys.stderr)
        return 1

    print(f"Adding voice={args.voice} for {len(keys)} {args.lang} phrases")

    import edge_tts  # noqa: PLC0415
    written, cached, failed = 0, 0, 0

    for key in sorted(keys):
        text = key[len(prefix):]
        h = alt_hash(args.lang, text, args.voice)
        rel = f"tts/{args.lang}/{h}.mp3"
        abs_path = OUT_DIR / args.lang / f"{h}.mp3"

        existing = manifest[key]
        if isinstance(existing, str):
            paths = [existing]
        elif isinstance(existing, list):
            paths = list(existing)
        else:
            paths = []

        if rel not in paths:
            paths.append(rel)
        manifest[key] = paths

        if abs_path.exists():
            cached += 1
            continue

        if args.dry_run:
            print(f"  [ new  ] {text!r} → {abs_path.name}")
            written += 1
            continue

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            t0 = time.perf_counter()
            async def _gen() -> None:
                comm = edge_tts.Communicate(text, voice=args.voice)
                await comm.save(str(abs_path))
            asyncio.run(_gen())
            dt = time.perf_counter() - t0
            print(f"  [ new  ] {text!r:30} → {abs_path.name}  ({dt:.2f}s)")
            written += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAILED {text!r}: {e}", file=sys.stderr)

    if not args.dry_run:
        save_manifest(manifest)
    print(f"\nDone. wrote={written} cached={cached} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
