"""Generate side-by-side TTS trials for a single kana to A/B which fix works best.

Writes results to `lingo/src/pub/trials/{tag}.mp3` so the dev server can
serve them via `/trials/{tag}.mp3` for browser-side comparison. The
companion HTML page `lingo/src/pub/i-audit.html` consumes these.

Each trial is a different approach to the edge-tts short-syllable bug:

- bare        — raw "い" (the broken baseline)
- period      — "い。" (trailing kuten — forces terminal-phrase prosody)
- slow        — Communicate rate="-25%" + "い。" (slower delivery, fuller articulation)
- slow_heavy  — rate="-40%" + "い。" (more conservative)
- slow_period — rate="-25%" + "。い。" (silence-bracketed slow)
- carrier     — current production approach (carrier wrap + silencedetect trim)
- kokoro      — Kokoro neural TTS (local, not Azure — different engine entirely)

Usage:
    .venv-tts/bin/python -m scripts.tts.short_kana_trials い
    .venv-tts/bin/python -m scripts.tts.short_kana_trials い --voice ja-JP-KeitaNeural
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
OUT_DIR = LINGO_FE / "src" / "pub" / "trials"


def slug(target: str, voice: str, tag: str) -> Path:
    voice_short = voice.replace("ja-JP-", "").replace("Neural", "").lower()
    return OUT_DIR / f"{target}_{voice_short}_{tag}.mp3"


async def edge_synth(
    text: str,
    voice: str,
    out: Path,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
) -> None:
    import edge_tts  # noqa: PLC0415
    comm = edge_tts.Communicate(text, voice=voice, rate=rate, volume=volume, pitch=pitch)
    await comm.save(str(out))


def kokoro_synth(text: str, out: Path) -> None:
    """Kokoro local neural TTS — alternative engine, no server trimming."""
    from kokoro import KPipeline  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415

    pipe = KPipeline(lang_code="j", repo_id="hexgrad/Kokoro-82M")
    chunks = []
    for result in pipe(text, voice="jf_alpha"):
        a = result.audio.numpy() if hasattr(result.audio, "numpy") else np.asarray(result.audio)
        chunks.append(a)
    if not chunks:
        raise RuntimeError("Kokoro produced no audio")
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    sf.write(out, audio, 24000, format="MP3")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("target", help="Single kana to trial, e.g. い")
    p.add_argument("--voice", default="ja-JP-NanamiNeural")
    p.add_argument("--skip-kokoro", action="store_true")
    args = p.parse_args()

    target = args.target
    voice = args.voice
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # tag, text, rate, volume, pitch, label
    trials: list[tuple[str, str, str, str, str, str]] = [
        ("bare",        target,           "+0%",   "+0%",  "+0Hz", f"Bare {target} (broken baseline)"),
        ("period",      f"{target}。",    "+0%",   "+0%",  "+0Hz", f"{target}。 (trailing kuten)"),
        ("slow",        f"{target}。",    "-25%",  "+0%",  "+0Hz", f"{target}。 at rate=-25%"),
        ("slow_heavy",  f"{target}。",    "-40%",  "+0%",  "+0Hz", f"{target}。 at rate=-40%"),
        ("slowest",     f"{target}。",    "-50%",  "+0%",  "+0Hz", f"{target}。 at rate=-50%"),
        ("slow_period", f"。{target}。",  "-25%",  "+0%",  "+0Hz", f"。{target}。 at rate=-25% (silence-bracketed)"),
        ("loud_slow",   f"{target}。",    "-40%",  "+40%", "+0Hz", f"{target}。 at rate=-40%, vol=+40%"),
        ("hai_carrier", f"はい、{target}、はい", "-20%", "+0%",  "+0Hz", f"はい、{target}、はい — synth only (no trim)"),
    ]

    print(f"Trials for {target!r} (voice={voice}) → {OUT_DIR.relative_to(REPO_ROOT)}")
    print()

    for tag, text, rate, volume, pitch, label in trials:
        out = slug(target, voice, tag)
        try:
            asyncio.run(edge_synth(text, voice, out, rate=rate, volume=volume, pitch=pitch))
            print(f"  ✓ {tag:12} {label}")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {tag:12} {e}", file=sys.stderr)

    # The "carrier_single" trial: synthesize "あ、{target}、あ" at slowed
    # rate, then silencedetect-trim the middle segment. Yields a single
    # mora instead of the doubled "いい" the current production carrier
    # produces. Slow rate forces Keita to articulate properly.
    try:
        import re
        import subprocess
        import tempfile

        carrier_rate = "-40%" if voice == "ja-JP-KeitaNeural" else "-25%"
        carrier_text = f"あ、{target}、あ"
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tmp = Path(tf.name)
        try:
            asyncio.run(edge_synth(carrier_text, voice, tmp, rate=carrier_rate))
            # silencedetect
            sd = subprocess.run(
                ["ffmpeg", "-loglevel", "info", "-i", str(tmp),
                 "-af", "silencedetect=noise=-40dB:d=0.04", "-f", "null", "-"],
                capture_output=True, text=True,
            )
            starts = [float(x) for x in re.findall(r"silence_start:\s+([\d.]+)", sd.stderr)]
            ends = [float(x) for x in re.findall(r"silence_end:\s+([\d.]+)", sd.stderr)]
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(tmp)],
                capture_output=True, text=True).stdout.strip())
            silences = list(zip(starts, ends))
            if len(starts) > len(ends):
                silences.append((starts[-1], dur))
            # Build audio segments
            segs: list[tuple[float, float]] = []
            prev_end = 0.0
            for s, e in silences:
                if s > prev_end + 0.005:
                    segs.append((prev_end, s))
                prev_end = e
            if prev_end < dur - 0.005:
                segs.append((prev_end, dur))
            # Filter tiny artifacts (breath, glottal clicks)
            real = [seg for seg in segs if seg[1] - seg[0] >= 0.06]
            if len(real) >= 2:
                ts, te = real[1]  # second real segment = the target
                out = slug(target, voice, "carrier_single")
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error",
                     "-ss", f"{max(0, ts-0.04):.3f}",
                     "-to", f"{te+0.06:.3f}",
                     "-i", str(tmp),
                     "-af", "apad=pad_dur=0.3",
                     "-c:a", "libmp3lame", "-q:a", "4", str(out)],
                    capture_output=True, check=True,
                )
                print(f"  ✓ carrier_single   single-mora carrier (rate={carrier_rate}) + silencedetect trim")
            else:
                print(f"  ! carrier_single   only {len(real)} real segments after filter — needed ≥2",
                      file=sys.stderr)
        finally:
            if tmp.exists():
                tmp.unlink()
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ carrier_single   {e}", file=sys.stderr)

    # The "carrier" trial: copy the current production file (which IS the
    # carrier+trim output we just regenerated). Primary voice uses
    # `(lang, text)` hashing; alt voices use `(lang, text, voice)`.
    import hashlib
    if voice == "ja-JP-NanamiNeural":
        h = hashlib.sha256(f"ja:{target}".encode("utf-8")).hexdigest()[:16]
    else:
        h = hashlib.sha256(f"ja:{target}::{voice}".encode("utf-8")).hexdigest()[:16]
    prod_path = LINGO_FE / "src" / "pub" / "tts" / "ja" / f"{h}.mp3"
    if prod_path.exists():
        import shutil
        shutil.copy(prod_path, slug(target, voice, "carrier"))
        print(f"  ✓ carrier      Current production (carrier-wrap + silencedetect trim)")
    else:
        print(f"  ! carrier      no production file at {prod_path}", file=sys.stderr)

    if not args.skip_kokoro:
        try:
            kokoro_synth(target, slug(target, voice, "kokoro"))
            print(f"  ✓ kokoro       Kokoro neural (local, jf_alpha voice)")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ kokoro       {e}", file=sys.stderr)

    print()
    print(f"Files in {OUT_DIR}:")
    for f in sorted(OUT_DIR.glob(f"{target}_*.mp3")):
        print(f"  /trials/{f.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
