"""Whisper-audit only the yōon entries (with phonetic normalization).

Same logic as scripts.tts.whisper_audit but scoped to the 33 yōon so we
don't re-transcribe single mora when we only care about yōon results.

Writes /tmp/whisper_yoon.csv for the audit page + downstream best-strategy.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
TTS_DIR = LINGO_FE / "src" / "pub" / "tts"
MANIFEST = TTS_DIR / "manifest.json"

YOON_ROWS = [
    ("k",          ["きゃ","きゅ","きょ"]),
    ("s",          ["しゃ","しゅ","しょ"]),
    ("ch",         ["ちゃ","ちゅ","ちょ"]),
    ("n",          ["にゃ","にゅ","にょ"]),
    ("h",          ["ひゃ","ひゅ","ひょ"]),
    ("m",          ["みゃ","みゅ","みょ"]),
    ("r",          ["りゃ","りゅ","りょ"]),
    ("g daku.",    ["ぎゃ","ぎゅ","ぎょ"]),
    ("j daku.",    ["じゃ","じゅ","じょ"]),
    ("b daku.",    ["びゃ","びゅ","びょ"]),
    ("p handaku.", ["ぴゃ","ぴゅ","ぴょ"]),
]


def primary_hash(text: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}".encode()).hexdigest()[:16]


def main() -> int:
    sys.path.insert(0, str(Path(__file__).parent))
    from faster_whisper import WhisperModel  # noqa: PLC0415
    from whisper_audit import categorize  # type: ignore

    print("Loading whisper model=small...", file=sys.stderr)
    model = WhisperModel("small", device="cpu", compute_type="int8")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows_out: list[dict] = []
    total: Counter = Counter()

    print(f"\n{'row':10} {'kana':5} {'cat':8} transcript")
    print("-" * 60)

    for row_name, kanas in YOON_ROWS:
        for kana in kanas:
            if f"ja:{kana}" not in manifest:
                continue
            path = TTS_DIR / "ja" / f"{primary_hash(kana)}.mp3"
            if not path.exists():
                continue
            segs, _info = model.transcribe(
                str(path), language="ja", beam_size=5,
                vad_filter=False, condition_on_previous_text=False,
            )
            t = "".join(s.text for s in segs).strip()
            cat = categorize(kana, t)
            total[cat] += 1
            print(f"{row_name:10} {kana:5} {cat:8} {t!r}")
            rows_out.append({
                "row": row_name, "kana": kana, "category": cat, "transcript": t,
            })

    print("\n=== Totals ===")
    for c in ("exact", "doubled", "partial", "wrong", "empty"):
        print(f"  {c:8} {total.get(c, 0)}")

    out = Path("/tmp/whisper_yoon.csv")
    with out.open("w", encoding="utf-8") as f:
        f.write("row,kana,voice,category,transcript,path\n")
        for r in rows_out:
            t = r["transcript"].replace(",", "")
            f.write(f"{r['row']},{r['kana']},Nanami,{r['category']},{t},.\n")
    print(f"\nCSV: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
