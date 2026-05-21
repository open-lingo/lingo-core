"""Transcribe every single-kana TTS file with Whisper, compare to intended target.

Why: ffmpeg energy-envelope analysis can't reliably distinguish a real
doubled-mora artifact ("いい" when we wanted "い") from natural
consonant-vowel structure (the closure-burst of "か"). Whisper hears
what a listener would hear and emits text we can string-compare.

Model: faster-whisper "small" — ~500 MB, ~5–15s per CPU transcribe.
Single-kana clips are sub-second so the whole sweep is a few minutes.

Categorization:
- exact      transcript == target (single mora pronounced correctly)
- doubled    transcript == target × 2 (the artifact we're hunting)
- partial    transcript contains target but with extra phonemes
- wrong      transcript doesn't contain target — wrong kana / dropped
- empty      whisper returned no text

Usage:
    .venv-tts/bin/python -m scripts.tts.whisper_audit
    .venv-tts/bin/python -m scripts.tts.whisper_audit --model base
    .venv-tts/bin/python -m scripts.tts.whisper_audit --csv /tmp/whisper.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
TTS_DIR = LINGO_FE / "src" / "pub" / "tts"
MANIFEST = TTS_DIR / "manifest.json"

ROWS: list[tuple[str, str]] = [
    ("vowel",       "あいうえお"),
    ("k",           "かきくけこ"),
    ("s",           "さしすせそ"),
    ("t",           "たちつてと"),
    ("n",           "なにぬねの"),
    ("h",           "はひふへほ"),
    ("m",           "まみむめも"),
    ("y",           "やゆよ"),
    ("r",           "らりるれろ"),
    ("w",           "わを"),
    ("ん",          "ん"),
    ("g daku.",     "がぎぐげご"),
    ("z daku.",     "ざじずぜぞ"),
    ("d daku.",     "だぢづでど"),
    ("b daku.",     "ばびぶべぼ"),
    ("p handaku.",  "ぱぴぷぺぽ"),
]


@dataclass
class Result:
    kana: str
    voice_short: str
    row: str
    path: Path
    transcript: str
    category: str


def primary_hash(text: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}".encode("utf-8")).hexdigest()[:16]


def alt_hash(text: str, voice: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}::{voice}".encode("utf-8")).hexdigest()[:16]


# Whisper transcribes Japanese phonetics; output isn't always written with the
# exact source kana. Normalize:
#   - drop punctuation, spaces, exclamations
#   - drop chōonpu (ー) and small-tsu (っ/ッ) — phonetically neutral markers
#   - map katakana → hiragana (same phoneme either way for our purposes)
#   - map historical を→お and ぢ→じ, づ→ず (modern pronunciation equivalence)
JUNK = "。、 ！？!?ーっッ"


def to_hiragana(ch: str) -> str:
    """Map a single katakana char to its hiragana equivalent. Other chars unchanged."""
    code = ord(ch)
    # Katakana block 0x30A1–0x30F6 maps to hiragana 0x3041–0x3096
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    return ch


PHONEME_EQUIV = {
    "を": "お",  # modern reading
    "ぢ": "じ",  # modern reading
    "づ": "ず",  # modern reading
}


def normalize(s: str) -> str:
    out = []
    for ch in s:
        if ch in JUNK:
            continue
        h = to_hiragana(ch)
        h = PHONEME_EQUIV.get(h, h)
        out.append(h)
    return "".join(out).strip()


def categorize(target: str, transcript: str) -> str:
    t = normalize(transcript)
    tgt = normalize(target)
    if not t:
        return "empty"
    if t == tgt:
        return "exact"
    if t == tgt * 2 or t == tgt + tgt:
        return "doubled"
    # transcript contains the target phoneme
    if tgt in t:
        if t.count(tgt) >= 2:
            return "doubled"
        return "partial"
    return "wrong"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="small", help="tiny / base / small / medium")
    p.add_argument("--csv", default=None)
    p.add_argument("--voice", default=None)
    p.add_argument("--show", choices=["all", "bad"], default="bad")
    args = p.parse_args()

    from faster_whisper import WhisperModel  # noqa: PLC0415

    print(f"Loading whisper model={args.model} (Japanese)...", file=sys.stderr)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    voices = [args.voice] if args.voice else ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"]

    results: list[Result] = []
    n_total = sum(1 for r,_ in ROWS for _ in _)  # noqa: B007
    n_done = 0
    for row_name, kanas in ROWS:
        for kana in kanas:
            if f"ja:{kana}" not in manifest:
                continue
            for v in voices:
                if v == "ja-JP-NanamiNeural":
                    h = primary_hash(kana)
                else:
                    h = alt_hash(kana, v)
                path = TTS_DIR / "ja" / f"{h}.mp3"
                if not path.exists():
                    continue
                voice_short = v.replace("ja-JP-", "").replace("Neural", "")
                segments, _info = model.transcribe(
                    str(path),
                    language="ja",
                    beam_size=5,
                    vad_filter=False,
                    condition_on_previous_text=False,
                )
                transcript = "".join(seg.text for seg in segments)
                cat = categorize(kana, transcript)
                results.append(Result(kana, voice_short, row_name, path, transcript.strip(), cat))
                n_done += 1
                if n_done % 10 == 0:
                    print(f"  ...{n_done} transcribed", file=sys.stderr)

    # Print
    rows = results if args.show == "all" else [r for r in results if r.category != "exact"]
    if rows:
        print(f"\n{'row':10} {'kana':4} {'voice':8} {'cat':8} {'transcript':<24}")
        print("-" * 68)
        for r in rows:
            print(f"{r.row:10} {r.kana:4} {r.voice_short:8} {r.category:8} {r.transcript!r:<24}")
    else:
        print("(everything exact!)")

    # Rollup
    print("\n=== Rollup by row × voice × category ===")
    bucket: dict[tuple[str, str], Counter] = {}
    for r in results:
        bucket.setdefault((r.row, r.voice_short), Counter())[r.category] += 1

    rollup_voices = [v.replace("ja-JP-","").replace("Neural","") for v in voices]
    header_cats = ["exact", "doubled", "partial", "wrong", "empty"]
    print(f"{'row':10} " + "  ".join(f"{v:8}" for v in rollup_voices) +
          "    " + "  ".join(f"{c:>7}" for c in header_cats))
    print("-" * 90)

    total_cat: Counter = Counter()
    for row_name, _ in ROWS:
        per_voice = []
        row_cats: Counter = Counter()
        for v in rollup_voices:
            b = bucket.get((row_name, v))
            if not b:
                per_voice.append(f"{'—':>8}")
                continue
            ok = b.get("exact", 0)
            tot = sum(b.values())
            per_voice.append(f"{ok}/{tot:<6}")
            row_cats.update(b)
        cells = [f"{row_cats.get(c, 0):>7}" if row_cats.get(c, 0) else f"{'·':>7}" for c in header_cats]
        print(f"{row_name:10} " + "  ".join(per_voice) + "    " + "  ".join(cells))
        total_cat.update(row_cats)

    print("\n=== Totals ===")
    for c in header_cats:
        print(f"  {c:8} {total_cat.get(c, 0)}")

    if args.csv:
        with Path(args.csv).open("w", encoding="utf-8") as f:
            f.write("row,kana,voice,category,transcript,path\n")
            for r in results:
                t = r.transcript.replace(",", "")
                f.write(f"{r.row},{r.kana},{r.voice_short},{r.category},{t},{r.path}\n")
        print(f"\nCSV: {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
