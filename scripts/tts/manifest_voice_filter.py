"""Restrict the voice rotation for specified manifest entries to a single voice.

Why: edge-tts Keita underpronounces single-mora vowels in a way that
can't be fixed with prosody knobs or carrier+trim (the only working
Keita fix yields two-mora audio). Surgically drop Keita from the voice
rotation for those entries so the manifest only emits the Nanami path.

The mp3 files on disk are not deleted — leaving them harmless and easy
to restore if we change strategy later.

Usage:
    .venv-tts/bin/python -m scripts.tts.manifest_voice_filter \
        --keep ja-JP-NanamiNeural --texts あ い う え お
    .venv-tts/bin/python -m scripts.tts.manifest_voice_filter \
        --keep ja-JP-NanamiNeural --texts あ い う え お --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
MANIFEST = LINGO_FE / "src" / "pub" / "tts" / "manifest.json"


def primary_hash(text: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}".encode()).hexdigest()[:16]


def alt_hash(text: str, voice: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}::{voice}".encode()).hexdigest()[:16]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--keep", required=True, help="Voice to retain, e.g. ja-JP-NanamiNeural")
    p.add_argument("--texts", nargs="+", required=True, help="Texts to filter")
    p.add_argument("--lang", default="ja")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    keep_path_primary = lambda text: f"tts/{args.lang}/{primary_hash(text, args.lang)}.mp3"

    changes = 0
    for text in args.texts:
        key = f"{args.lang}:{text}"
        if key not in manifest:
            print(f"  ! {key} not in manifest", file=sys.stderr)
            continue
        # Primary voice is the one hashed without voice suffix (Nanami in our setup).
        # If keep is the primary, we narrow to just that one path.
        if args.keep == "ja-JP-NanamiNeural":
            new_value = [keep_path_primary(text)]
        else:
            new_value = [f"tts/{args.lang}/{alt_hash(text, args.keep, args.lang)}.mp3"]
        if manifest[key] == new_value:
            print(f"  = {key} already narrowed")
            continue
        print(f"  → {key}: {manifest[key]} → {new_value}")
        if not args.dry_run:
            manifest[key] = new_value
        changes += 1

    if not args.dry_run and changes:
        MANIFEST.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"\n{'(dry-run) ' if args.dry_run else ''}Updated {changes} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
