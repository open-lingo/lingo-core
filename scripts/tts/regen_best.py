"""For each currently-wrong single-kana file, try every priming strategy,
keep the one Whisper rates highest, and overwrite the production file.

Strategies tried per kana (in priority order on tie):
  baseline       {k}。
  sokuon_pre     っ{k}。
  sokuon_both    っ{k}っ。
  sokuon_slow    っ{k}。     rate=-25%
  sokuon_pre_slow {k}。       rate=-25%   (plain slow, no sokuon — control)

Picks `exact` over `partial` over `wrong`. Within the same category,
prefers shorter prompt prosody (baseline > sokuon_pre > others).

Usage:
    .venv-tts/bin/python -m scripts.tts.regen_best
    .venv-tts/bin/python -m scripts.tts.regen_best --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_CORE = REPO_ROOT / "lingo-core"
LINGO_FE = REPO_ROOT / "lingo"
TTS_DIR = LINGO_FE / "src" / "pub" / "tts"

VOICE = "ja-JP-NanamiNeural"
TRAILING_SILENCE = 0.30

STRATEGIES = [
    # tag, template (uses {k}), rate, priority_rank
    ("baseline",        "{k}。",      "+0%",  0),
    ("sokuon_pre",      "っ{k}。",    "+0%",  1),
    ("plain_slow",      "{k}。",      "-25%", 2),
    ("sokuon_both",     "っ{k}っ。",  "+0%",  3),
    ("sokuon_slow",     "っ{k}。",    "-25%", 4),
]

CATEGORY_RANK = {"exact": 0, "partial": 1, "doubled": 2, "wrong": 3, "empty": 4}


def primary_hash(text: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}".encode()).hexdigest()[:16]


async def synth(text: str, voice: str, out: Path, rate: str = "+0%", attempts: int = 5) -> None:
    """Synthesize with exponential backoff on 503 / rate-limit errors."""
    import edge_tts  # noqa: PLC0415
    last: Exception | None = None
    for i in range(attempts):
        try:
            comm = edge_tts.Communicate(text, voice=voice, rate=rate)
            await comm.save(str(out))
            return
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(2 ** i)
    raise last  # type: ignore[misc]


def pad_in_place(src: Path, dst: Path) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-af", f"apad=pad_dur={TRAILING_SILENCE}",
         "-c:a", "libmp3lame", "-q:a", "4", str(dst)],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--csv", default="/tmp/whisper_after.csv",
                    help="CSV of prior whisper audit. Non-exact rows get retried.")
    args = ap.parse_args()

    # Reload current whisper results so we know which kana to fix.
    sys.path.insert(0, str(LINGO_CORE / "scripts" / "tts"))
    import csv

    from faster_whisper import WhisperModel  # noqa: PLC0415
    from whisper_audit import categorize  # type: ignore
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"No {csv_path} — run whisper_audit first.", file=sys.stderr)
        return 1

    needs_fix: list[tuple[str, str, str]] = []  # (row, kana, current_cat)
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            if row["category"] != "exact":
                needs_fix.append((row["row"], row["kana"], row["category"]))
    print(f"{len(needs_fix)} kana need fixing (non-exact in last audit)")

    print("\nLoading Whisper (small)...", file=sys.stderr)
    model = WhisperModel("small", device="cpu", compute_type="int8")

    improved = 0
    unchanged = 0
    log: list[tuple[str, str, str, str, str, str]] = []  # row,kana,prev_cat,new_cat,strategy,heard

    for row_name, kana, prev_cat in needs_fix:
        # Try every strategy, transcribe each, pick the best.
        results: list[tuple[str, str, str, str, int]] = []  # cat, transcript, strategy, mp3path, priority
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            for tag, tmpl, rate, prio in STRATEGIES:
                text = tmpl.replace("{k}", kana)
                raw = tdp / f"raw_{tag}.mp3"
                padded = tdp / f"pad_{tag}.mp3"
                try:
                    asyncio.run(synth(text, VOICE, raw, rate=rate))
                    if not pad_in_place(raw, padded):
                        continue
                except Exception as e:  # noqa: BLE001
                    print(f"  fail {kana}/{tag}: {e}", file=sys.stderr)
                    continue
                segs, _info = model.transcribe(
                    str(padded), language="ja", beam_size=5,
                    vad_filter=False, condition_on_previous_text=False,
                )
                t = "".join(s.text for s in segs).strip()
                cat = categorize(kana, t)
                # Sort key: (category rank, priority rank) — lower is better.
                results.append((cat, t, tag, str(padded), prio))

            if not results:
                continue
            results.sort(key=lambda r: (CATEGORY_RANK.get(r[0], 99), r[4]))
            best_cat, best_transcript, best_tag, best_path, _ = results[0]

            # Compare to previous category — only overwrite if improved.
            if CATEGORY_RANK.get(best_cat, 99) < CATEGORY_RANK.get(prev_cat, 99):
                target = TTS_DIR / "ja" / f"{primary_hash(kana)}.mp3"
                if not args.dry_run:
                    shutil.copy(best_path, target)
                improved += 1
                log.append((row_name, kana, prev_cat, best_cat, best_tag, best_transcript))
                print(f"  ↑ {kana} {prev_cat:7} → {best_cat:7} via {best_tag:12} (heard {best_transcript!r})")
            else:
                unchanged += 1
                log.append((row_name, kana, prev_cat, best_cat, best_tag, best_transcript))
                print(f"  = {kana} stays {prev_cat:7} (best was {best_cat} via {best_tag}, heard {best_transcript!r})")

    print(f"\nDone. improved={improved} unchanged={unchanged} {'(dry-run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
