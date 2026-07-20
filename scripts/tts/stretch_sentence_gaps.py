"""Stretch inter-sentence pauses inside multi-sentence TTS clips.

Why: dialogue lines like わたしは トムだ。がくせいだ。 are synthesized as
ONE edge-tts request — that keeps a single consistent prosody (chaining
independently-synthesized per-sentence clips made the pitch jump between
sentences, Spencer QA 2026-07-19) — but Nanami's natural inter-sentence
pause is short, so sentences read run-together. This script finds the
internal silences in each multi-sentence clip and stretches them to a
minimum duration, giving learners a breath between sentences without
touching the voice itself.

Scope: every manifest key whose text contains an INTERNAL sentence
terminator (。？！ followed by more text). Detection via ffmpeg
silencedetect; splicing via one ffmpeg filter graph per file (atrim +
aevalsrc silence + concat).

Idempotent: tracks processed keys in a sidecar `.gapstretched` marker.
Re-run after regenerating clips with --force (or delete the marker).

Usage:
    .venv-tts/bin/python -m scripts.tts.stretch_sentence_gaps
    .venv-tts/bin/python -m scripts.tts.stretch_sentence_gaps --gap-ms 450
    .venv-tts/bin/python -m scripts.tts.stretch_sentence_gaps --force
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
TTS_DIR = LINGO_FE / "src" / "pub" / "tts"
MANIFEST = TTS_DIR / "manifest.json"
MARKER = TTS_DIR / ".gapstretched"

def is_multi_sentence(text: str) -> bool:
    """True when a sentence terminator has real content after it. Mirrors
    splitJaSentences in lingo DialogueListenStepView.tsx; the rstrip keeps
    a trailing 。」 (quoted single sentence) from counting as internal."""
    return bool(re.search(r"[。？！]", text.rstrip("」。？！")))

SILENCE_RE = re.compile(
    r"silence_(start|end): ([0-9.]+)"
)


def load_marker() -> set[str]:
    if not MARKER.exists():
        return set()
    return {line.strip() for line in MARKER.read_text().splitlines() if line.strip()}


def save_marker(seen: set[str]) -> None:
    MARKER.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")


def detect_silences(src: Path, noise_db: int, min_len_ms: int) -> list[tuple[float, float]]:
    """Return [(start, end), ...] silences via ffmpeg silencedetect."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(src),
            "-af", f"silencedetect=noise={noise_db}dB:d={min_len_ms / 1000.0}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    events: list[tuple[str, float]] = [
        (m.group(1), float(m.group(2))) for m in SILENCE_RE.finditer(result.stderr)
    ]
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for kind, t in events:
        if kind == "start":
            start = t
        elif kind == "end" and start is not None:
            silences.append((start, t))
            start = None
    return silences


def clip_duration(src: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(src),
        ],
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def stretch_file(src: Path, gap_ms: int, expected_gaps: int) -> bool:
    """Insert silence at internal sentence pauses. True if the file changed."""
    duration = clip_duration(src)
    # Internal silences only — ignore anything hugging the ends (leading
    # breath room / the pad_silence tail).
    # Nanami's inter-sentence pause is short (~80-130ms) and not fully
    # silent (breath tail), so detect at -30dB with a 60ms floor and let
    # the longest-N pick below separate sentence pauses from word gaps.
    silences = [
        (s, e)
        for s, e in detect_silences(src, noise_db=-30, min_len_ms=60)
        if s > 0.15 and e < duration - 0.15
    ]
    if not silences:
        return False
    # Keep the longest `expected_gaps` silences — short intra-sentence dips
    # (mora boundaries) must not become sentence breaks.
    silences = sorted(
        sorted(silences, key=lambda p: p[1] - p[0], reverse=True)[:expected_gaps]
    )
    gap_s = gap_ms / 1000.0
    inserts = [
        (s + e) / 2.0
        for s, e in silences
        if (e - s) < gap_s  # already long enough → leave alone
    ]
    if not inserts:
        return False

    # Build one filter graph: segment clips around each insert point with
    # generated silence between.
    parts: list[str] = []
    labels: list[str] = []
    bounds = [0.0, *inserts, None]
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        trim = f"atrim=start={lo}" + (f":end={hi}" if hi is not None else "")
        parts.append(f"[0:a]{trim},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[a{i}]")
        if hi is not None:
            parts.append(
                f"aevalsrc=0:d={gap_s}:s=24000,aformat=channel_layouts=mono[s{i}]"
            )
            labels.append(f"[s{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(src),
                "-filter_complex", graph,
                "-map", "[out]",
                "-c:a", "libmp3lame", "-q:a", "4",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  ffmpeg failed for {src.name}: {result.stderr.strip()[:200]}", file=sys.stderr)
            return False
        shutil.move(str(tmp_path), str(src))
        return True
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap-ms", type=int, default=450)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--lang", default="ja")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    seen = set() if args.force else load_marker()
    prefix = f"{args.lang}:"

    processed = 0
    changed = 0
    for key, value in manifest.items():
        if not key.startswith(prefix):
            continue
        text = key[len(prefix):]
        if not is_multi_sentence(text):
            continue
        if key in seen:
            continue
        paths = value if isinstance(value, list) else [value]
        expected = max(1, len(re.findall(r"[。？！]", text.rstrip("。？！」"))))
        for rel in paths:
            mp3 = LINGO_FE / "src" / "pub" / rel
            if not mp3.exists():
                print(f"  missing file for {key}: {rel}", file=sys.stderr)
                continue
            processed += 1
            if stretch_file(mp3, args.gap_ms, expected):
                changed += 1
        seen.add(key)

    save_marker(seen)
    print(f"Done. examined={processed} stretched={changed}")
    return 0


if __name__ == "__main__":
    main()
