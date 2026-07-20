"""Synthesize male dialogue-speaker lines with the Keita voice.

Two-voice dialogues (Spencer 2026-07-19): male-named speakers (Tom, Ken,
Tanaka) speak with ja-JP-KeitaNeural; everyone else stays on the default
Nanami corpus. No pitch processing anywhere — real voices, raw clips.

Reads `test_decks/ja-keita-dialogue.json` (auto-emitted by lingo
scripts/emit-tts-deck.mjs) and writes clips into the shared tts dir with
manifest keys `ja-keita:<text>` — the frontend resolver builds keys as
`<lang>:<text>`, so the dialogue view just passes lang="ja-keita" for
male speakers and falls back to plain "ja" when a clip is missing.

Usage:
    .venv-tts/bin/python -m scripts.tts.gen_keita_dialogue
    .venv-tts/bin/python -m scripts.tts.gen_keita_dialogue --force
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import edge_tts

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
OUT_DIR = LINGO_FE / "src" / "pub" / "tts"
MANIFEST = OUT_DIR / "manifest.json"
DECK = REPO_ROOT / "lingo-core" / "test_decks" / "ja-keita-dialogue.json"

VOICE = "ja-JP-KeitaNeural"
KEY_PREFIX = "ja-keita:"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    deck = json.loads(DECK.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    (OUT_DIR / "ja").mkdir(parents=True, exist_ok=True)

    wrote = skipped = failed = 0
    for card in deck["cards"]:
        text = card["front"]
        key = f"{KEY_PREFIX}{text}"
        name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".mp3"
        rel = f"tts/ja/{name}"
        out = OUT_DIR / "ja" / name
        if not args.force and manifest.get(key) == rel and out.exists():
            skipped += 1
            continue
        try:
            asyncio.run(edge_tts.Communicate(text, voice=VOICE).save(str(out)))
            manifest[key] = rel
            wrote += 1
            print(f"  [{wrote}] {text!r} → {name}")
        except Exception as e:  # noqa: BLE001 — report and continue
            failed += 1
            print(f"  FAILED {text!r}: {e}", file=sys.stderr)

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Done. wrote={wrote} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
