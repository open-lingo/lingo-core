"""Test sokuon-prefix and other priming strategies on the Whisper-wrong kana.

Hypothesis (per user): a leading small-tsu っ forces edge-tts to close the
glottis before the target, releasing into a cleaner articulation. We test
this against the current baseline plus a few related variants and run
Whisper on each to see which actually improve the wrong cases.

Output: writes mp3s into `lingo/src/pub/trials/sokuon/{kana}_{tag}.mp3`,
runs Whisper, prints a per-kana table showing which strategies cleared
the wrong-category bar.

Usage:
    .venv-tts/bin/python -m scripts.tts.sokuon_trials
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
OUT_DIR = LINGO_FE / "src" / "pub" / "trials" / "sokuon"

# Whisper-wrong / partial cases from the regen audit.
WRONG = list("はひふへほ" "ばびぶべぼ" "ぱぴぺ" "らりろ" "げずどち")


async def synth(text: str, out: Path, voice: str = "ja-JP-NanamiNeural", rate: str = "+0%") -> None:
    import edge_tts  # noqa: PLC0415
    comm = edge_tts.Communicate(text, voice=voice, rate=rate)
    await comm.save(str(out))


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # tag, text-template (uses {k} placeholder), rate, label
    STRATS = [
        ("baseline",        "{k}。",        "+0%",  "current production"),
        ("sokuon_pre",      "っ{k}。",      "+0%",  "small-tsu prefix"),
        ("sokuon_both",     "っ{k}っ。",    "+0%",  "small-tsu on both sides"),
        ("sokuon_slow",     "っ{k}。",      "-25%", "small-tsu prefix + slow rate"),
        ("vowel_pre",       "あ{k}。",      "+0%",  "あ as carrier prefix (no trim)"),
        ("vowel_pre_slow",  "あ{k}。",      "-25%", "あ carrier + slow rate"),
    ]

    print(f"Generating {len(WRONG)} kana × {len(STRATS)} strategies = {len(WRONG) * len(STRATS)} files")
    for kana in WRONG:
        for tag, tmpl, rate, _ in STRATS:
            text = tmpl.replace("{k}", kana)
            out = OUT_DIR / f"{kana}_{tag}.mp3"
            try:
                asyncio.run(synth(text, out, rate=rate))
            except Exception as e:  # noqa: BLE001
                print(f"  FAIL {kana}/{tag}: {e}", file=sys.stderr)

    print("\nTranscribing with Whisper (small)...")
    sys.path.insert(0, str(Path(__file__).parent))
    from faster_whisper import WhisperModel  # noqa: PLC0415
    from whisper_audit import categorize  # type: ignore

    model = WhisperModel("small", device="cpu", compute_type="int8")
    results: dict[str, dict[str, tuple[str, str]]] = {}
    for kana in WRONG:
        results[kana] = {}
        for tag, _, _, _ in STRATS:
            p = OUT_DIR / f"{kana}_{tag}.mp3"
            if not p.exists():
                continue
            segs, _info = model.transcribe(
                str(p), language="ja", beam_size=5, vad_filter=False, condition_on_previous_text=False,
            )
            t = "".join(s.text for s in segs).strip()
            cat = categorize(kana, t)
            results[kana][tag] = (cat, t)

    # Per-kana row
    print()
    head = f"{'kana':4}" + "  ".join(f"{tag:14}" for tag, *_ in STRATS)
    print(head)
    print("-" * len(head))
    for kana in WRONG:
        cells = []
        for tag, *_ in STRATS:
            cat, t = results[kana].get(tag, ("-", ""))
            mark = "✓" if cat == "exact" else "~" if cat == "partial" else "✗"
            cells.append(f"{mark} {cat:6} {t[:5]!r:<7}")
        print(f"{kana:4}" + "  ".join(cells))

    # Per-strategy tally
    print("\n=== Per-strategy tally ===")
    print(f"{'strategy':16} {'exact':>5} {'partial':>7} {'wrong':>5} {'empty':>5}")
    for tag, _, _, _ in STRATS:
        from collections import Counter
        c = Counter(results[k][tag][0] for k in WRONG if tag in results[k])
        print(f"{tag:16} {c.get('exact',0):>5} {c.get('partial',0):>7} {c.get('wrong',0):>5} {c.get('empty',0):>5}")

    # Best-strategy-per-kana
    print("\n=== Best strategy per kana ===")
    PRIORITY = ["exact", "partial", "doubled", "wrong", "empty"]
    for kana in WRONG:
        best_tag = None
        best_rank = 99
        for tag, *_ in STRATS:
            cat = results[kana].get(tag, ("wrong",""))[0]
            try:
                rank = PRIORITY.index(cat)
            except ValueError:
                rank = 99
            if rank < best_rank:
                best_rank = rank
                best_tag = tag
        cat, t = results[kana][best_tag] if best_tag else ("wrong","")
        print(f"  {kana} → {best_tag:14} ({cat}, heard={t!r})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
