"""Regenerate single-kana TTS files using a carrier wrap, then trim.

Why: edge-tts truncates / underpronounces single-syllable utterances
("い" alone sounds like "eek" or barely plays at all in Keita's voice).
The audio gets a not-quite-complete final frame AND in some voices the
TTS engine simply rushes through the utterance.

Fix: synthesize the target inside a carrier ("あ、{target}{target}、あ"),
which forces edge-tts into "sentence mode" so the target syllable is
fully pronounced. Then use ffmpeg silencedetect to locate the middle
audio segment (which is the doubled target), extract it with small
pad-ins for natural ramp, and append trailing silence so any decoder
flush still has room.

The extracted segment is written to the exact same hash path the main
generator uses, so the manifest is untouched and consumers see the new
audio immediately.

Usage:
    .venv-tts/bin/python -m scripts.tts.regen_short_kana
    .venv-tts/bin/python -m scripts.tts.regen_short_kana --dry-run
    .venv-tts/bin/python -m scripts.tts.regen_short_kana --only い
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
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
MARKER = TTS_DIR / ".padded"

DEFAULT_VOICE = "ja-JP-NanamiNeural"
ALT_VOICE = "ja-JP-KeitaNeural"

# Silence detect threshold (dBFS) and minimum gap (seconds).
SILENCE_DB = -40
SILENCE_MIN = 0.04

# Padding around the extracted target (seconds).
LEAD_PAD = 0.04
TAIL_PAD = 0.06
TRAILING_SILENCE = 0.30


def primary_hash(text: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}".encode("utf-8")).hexdigest()[:16]


def alt_hash(text: str, voice: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}::{voice}".encode("utf-8")).hexdigest()[:16]


def carrier_text(target: str) -> str:
    """Wrap a single kana so edge-tts pronounces it as a full mid-sentence syllable."""
    return f"あ、{target}{target}、あ"


async def synth(text: str, voice: str, out: Path) -> None:
    import edge_tts  # noqa: PLC0415
    comm = edge_tts.Communicate(text, voice=voice)
    await comm.save(str(out))


def detect_silences(path: Path) -> list[tuple[float, float]]:
    """Return list of (start, end) silence intervals using ffmpeg silencedetect."""
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "info", "-i", str(path),
         "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = re.findall(r"silence_start:\s+([\d.]+)", result.stderr)
    ends = re.findall(r"silence_end:\s+([\d.]+)", result.stderr)
    intervals: list[tuple[float, float]] = []
    for s, e in zip(starts, ends):
        intervals.append((float(s), float(e)))
    # Sometimes silencedetect emits a trailing silence_start without an end
    # (file ends in silence). Cap with file duration.
    if len(starts) > len(ends):
        dur = ffprobe_duration(path)
        intervals.append((float(starts[-1]), dur))
    return intervals


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def audio_segments(silences: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Convert silence intervals → audio segments (the complement)."""
    segs: list[tuple[float, float]] = []
    prev_end = 0.0
    for s, e in silences:
        if s > prev_end + 0.005:  # ignore zero-length gaps
            segs.append((prev_end, s))
        prev_end = e
    if prev_end < duration - 0.005:
        segs.append((prev_end, duration))
    return segs


def extract_segment(src: Path, start: float, end: float, dst: Path) -> bool:
    """Extract [start, end] from src into dst, with trailing silence."""
    s = max(0.0, start - LEAD_PAD)
    e = end + TAIL_PAD
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{s:.3f}",
         "-to", f"{e:.3f}",
         "-i", str(src),
         "-af", f"apad=pad_dur={TRAILING_SILENCE}",
         "-c:a", "libmp3lame", "-q:a", "4",
         str(dst)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  FAILED extract for {dst.name}: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def regen_one(target: str, voice: str, out_hash: str, dry: bool) -> bool:
    """Generate carrier + extract middle target → write to TTS_DIR/ja/{out_hash}.mp3."""
    out_path = TTS_DIR / "ja" / f"{out_hash}.mp3"
    if dry:
        print(f"  [dry] {target!r} ({voice}) → {out_path.name}")
        return True

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
        carrier_path = Path(tf.name)
    try:
        text = carrier_text(target)
        asyncio.run(synth(text, voice, carrier_path))
        duration = ffprobe_duration(carrier_path)
        silences = detect_silences(carrier_path)
        segs = audio_segments(silences, duration)
        if len(segs) < 2:
            print(f"  WARN {target!r}/{voice}: only {len(segs)} audio segments, falling back to whole file",
                  file=sys.stderr)
            shutil.move(str(carrier_path), str(out_path))
            return True
        # Take the middle segment (segs[1]) — the target.
        # If only 2 segments, segs[1] is everything-after-first-silence, which
        # for our carrier ends up being "{target}{target} ... " — still usable.
        target_seg = segs[1]
        ok = extract_segment(carrier_path, target_seg[0], target_seg[1], out_path)
        return ok
    finally:
        if carrier_path.exists():
            carrier_path.unlink()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", default=None, help="Comma-separated targets, e.g. い,あ")
    args = p.parse_args()

    if not MANIFEST.exists():
        print(f"No manifest at {MANIFEST}", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    targets: list[str] = []
    for key in sorted(manifest):
        if not key.startswith("ja:"):
            continue
        text = key[3:]
        if len(text) != 1:
            continue
        targets.append(text)

    if args.only:
        wanted = {t.strip() for t in args.only.split(",") if t.strip()}
        targets = [t for t in targets if t in wanted]

    print(f"Regenerating {len(targets)} single-kana entries (× 2 voices = {len(targets)*2} files)")

    seen = set()
    if MARKER.exists():
        seen = {line.strip() for line in MARKER.read_text().splitlines() if line.strip()}

    done, failed = 0, 0
    for kana in targets:
        for voice in (DEFAULT_VOICE, ALT_VOICE):
            if voice == DEFAULT_VOICE:
                h = primary_hash(kana)
            else:
                h = alt_hash(kana, voice)
            rel = f"ja/{h}.mp3"
            ok = regen_one(kana, voice, h, args.dry_run)
            if ok:
                done += 1
                seen.add(rel)  # already padded — mark as such so pad_silence skips
                if done % 10 == 0:
                    print(f"  ...{done} done")
            else:
                failed += 1

    if not args.dry_run:
        MARKER.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")
    print(f"\nDone. ok={done} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
