"""Acoustic-features analysis of single-kana TTS files via ffmpeg.

What this checks (no audio-library deps — just ffmpeg + ffprobe):

- **voiced_segments** — count of audio gaps between silences. For a
  proper single-mora kana this should be 1. For a doubled-mora artifact
  (the current `あ、いい、あ`+trim output) it's often 2. For a
  dropped/silent file it's 0.
- **target_dur_ms** — total non-silent duration. Healthy mora ≈ 100–280 ms.
- **rms_db** — overall loudness. Healthy ≈ −20 to −12 dB. Below −30 dB
  hints at an under-pronounced "ghost" mora.
- **silence_ratio** — silent fraction of the file. Carrier+trim files
  always have trailing-silence padding, so 0.4–0.6 is expected.

Categorization heuristic per file:
- `single`   1 voiced segment ≥ 80 ms, rms ≥ −24 dB
- `doubled`  2 voiced segments, both ≥ 60 ms
- `short`    1 voiced segment < 80 ms, OR rms < −28 dB
- `silent`   0 voiced segments OR target_dur_ms < 30 ms
- `multi`    ≥ 3 voiced segments (something weird)

Per-voice rollup at the end shows how each row scored.

Usage:
    .venv-tts/bin/python -m scripts.tts.analyze_kana
    .venv-tts/bin/python -m scripts.tts.analyze_kana --voice ja-JP-KeitaNeural
    .venv-tts/bin/python -m scripts.tts.analyze_kana --csv /tmp/kana.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
TTS_DIR = LINGO_FE / "src" / "pub" / "tts"
MANIFEST = TTS_DIR / "manifest.json"

SILENCE_DB = -36
SILENCE_MIN = 0.04
ARTIFACT_MIN = 0.06   # ignore segments below this length when counting mora


# Row taxonomy — we use this for the per-row rollup at the end.
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
class Sample:
    kana: str
    voice_short: str
    row: str
    path: Path
    duration_ms: float
    target_dur_ms: float
    voiced_segments: int
    rms_db: float
    silence_ratio: float
    category: str


def primary_hash(text: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}".encode()).hexdigest()[:16]


def alt_hash(text: str, voice: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}::{voice}".encode()).hexdigest()[:16]


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip()) if r.stdout.strip() else 0.0


def ffmpeg_silences(path: Path) -> list[tuple[float, float]]:
    r = subprocess.run(
        ["ffmpeg", "-loglevel", "info", "-i", str(path),
         "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start:\s+([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s+([\d.]+)", r.stderr)]
    silences = list(zip(starts, ends))
    if len(starts) > len(ends):
        silences.append((starts[-1], ffprobe_duration(path)))
    return silences


def ffmpeg_rms_db(path: Path) -> float:
    r = subprocess.run(
        ["ffmpeg", "-loglevel", "info", "-i", str(path),
         "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    matches = re.findall(r"RMS level dB:\s+(-?[\d.]+)", r.stderr)
    if not matches:
        return -120.0
    # `astats` emits per-frame plus overall. Last value is the cumulative.
    return float(matches[-1])


def audio_segments(silences: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    segs: list[tuple[float, float]] = []
    prev_end = 0.0
    for s, e in silences:
        if s > prev_end + 0.005:
            segs.append((prev_end, s))
        prev_end = e
    if prev_end < duration - 0.005:
        segs.append((prev_end, duration))
    return segs


def categorize(
    target_ms: float,
    voiced: int,
    rms_db: float,
    segs_real: list[tuple[float, float]],
) -> str:
    """Classify a kana audio file.

    True doubled-mora "いい" pattern: two segments BOTH ≥ 100 ms with a
    clear silence gap (≥ 80 ms) between them. Distinguish from natural
    consonant-vowel structure ("か" = brief burst + ~150 ms vowel; the
    closure looks like a silence gap but is short and the leading
    segment is tiny).
    """
    if voiced == 0 or target_ms < 30:
        return "silent"
    if voiced >= 3:
        return "multi"
    if voiced == 2:
        a, b = segs_real
        a_dur = (a[1] - a[0]) * 1000
        b_dur = (b[1] - b[0]) * 1000
        gap = (b[0] - a[1]) * 1000
        # Both segments substantial AND clear silence between them → doubled mora.
        if a_dur >= 100 and b_dur >= 100 and gap >= 80:
            return "doubled"
        # Otherwise it's CV structure (consonant burst + vowel), treat as single.
        # But if total target is too short or too quiet, still flag short.
        if target_ms < 100 or rms_db < -28:
            return "short"
        return "single"
    # voiced == 1
    if target_ms < 80 or rms_db < -28:
        return "short"
    return "single"


def analyze_one(kana: str, voice: str, row: str) -> Sample | None:
    voice_short = voice.replace("ja-JP-", "").replace("Neural", "")
    if voice == "ja-JP-NanamiNeural":
        h = primary_hash(kana)
    else:
        h = alt_hash(kana, voice)
    path = TTS_DIR / "ja" / f"{h}.mp3"
    if not path.exists():
        return None
    duration = ffprobe_duration(path)
    silences = ffmpeg_silences(path)
    segs = audio_segments(silences, duration)
    real = [s for s in segs if s[1] - s[0] >= ARTIFACT_MIN]
    target = sum(s[1] - s[0] for s in real)
    rms = ffmpeg_rms_db(path)
    silence_ratio = 1.0 - (target / duration) if duration > 0 else 1.0
    cat = categorize(target * 1000, len(real), rms, real)
    return Sample(
        kana=kana, voice_short=voice_short, row=row, path=path,
        duration_ms=duration * 1000, target_dur_ms=target * 1000,
        voiced_segments=len(real), rms_db=rms, silence_ratio=silence_ratio,
        category=cat,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--voice", default=None, help="Restrict to one voice")
    p.add_argument("--csv", default=None, help="Write CSV to this path")
    p.add_argument("--show", choices=["all", "bad", "row"], default="bad",
                   help="Per-file output: all rows / only non-`single` / one-per-row")
    args = p.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    voices = [args.voice] if args.voice else ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"]

    samples: list[Sample] = []
    for row_name, kanas in ROWS:
        for kana in kanas:
            if f"ja:{kana}" not in manifest:
                continue
            for v in voices:
                s = analyze_one(kana, v, row_name)
                if s:
                    samples.append(s)

    # Per-file table
    if args.show == "all":
        rows = samples
    elif args.show == "row":
        seen = set()
        rows = []
        for s in samples:
            key = (s.row, s.voice_short)
            if key in seen:
                continue
            seen.add(key)
            rows.append(s)
    else:
        rows = [s for s in samples if s.category != "single"]

    if rows:
        print(f"{'row':10} {'kana':4} {'voice':8} {'cat':8} {'dur ms':>7} {'tgt ms':>7} {'seg':>3} {'rms dB':>7}")
        print("-" * 70)
        for s in rows:
            print(f"{s.row:10} {s.kana:4} {s.voice_short:8} {s.category:8} "
                  f"{s.duration_ms:7.0f} {s.target_dur_ms:7.0f} {s.voiced_segments:>3} {s.rms_db:7.1f}")
    else:
        print("(no rows matched --show filter)")

    # Per-row rollup by voice + category
    print("\n=== Rollup by row × voice × category ===")
    bucket: dict[tuple[str, str], Counter] = {}
    for s in samples:
        bucket.setdefault((s.row, s.voice_short), Counter())[s.category] += 1

    rollup_voices = [v.replace("ja-JP-","").replace("Neural","") for v in voices]
    header_cats = ["single", "doubled", "short", "silent", "multi"]
    head = f"{'row':10} " + "  ".join(f"{v:8}" for v in rollup_voices)
    print(head + "    " + "  ".join(f"{c:>7}" for c in header_cats))
    print("-" * len(head + "    " + "  ".join(f"{c:>7}" for c in header_cats)))

    for row_name, _ in ROWS:
        per_voice: list[str] = []
        per_cat: Counter = Counter()
        for v in rollup_voices:
            b = bucket.get((row_name, v))
            if not b:
                per_voice.append(f"{'—':>8}")
                continue
            total = sum(b.values())
            good = b.get("single", 0)
            tag = f"{good}/{total}"
            per_voice.append(f"{tag:>8}")
            per_cat.update(b)
        cat_cells = []
        for cat in header_cats:
            n = per_cat.get(cat, 0)
            cat_cells.append(f"{n:>7}" if n else f"{'·':>7}")
        print(f"{row_name:10} " + "  ".join(per_voice) + "    " + "  ".join(cat_cells))

    # Optional CSV
    if args.csv:
        out = Path(args.csv)
        with out.open("w", encoding="utf-8") as f:
            f.write("row,kana,voice,category,duration_ms,target_dur_ms,voiced_segments,rms_db,silence_ratio,path\n")
            for s in samples:
                f.write(f"{s.row},{s.kana},{s.voice_short},{s.category},"
                        f"{s.duration_ms:.1f},{s.target_dur_ms:.1f},{s.voiced_segments},"
                        f"{s.rms_db:.2f},{s.silence_ratio:.3f},{s.path}\n")
        print(f"\nCSV: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
