"""Generate Nanami TTS for the 33 hiragana yōon (combination kana).

Yōon = consonant + small ya/yu/yo (ゃ/ゅ/ょ). They're 2 unicode codepoints
but represent a single mora. We use the same trailing-period approach as
single mora — `{kana}。` — then run regen_best as a self-healing pass to
fix any that don't transcribe exactly.

Usage:
    .venv-tts/bin/python -m scripts.tts.gen_yoon
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINGO_FE = REPO_ROOT / "lingo"
TTS_DIR = LINGO_FE / "src" / "pub" / "tts"
MANIFEST = TTS_DIR / "manifest.json"
MARKER = TTS_DIR / ".padded"

VOICE = "ja-JP-NanamiNeural"
TRAILING_SILENCE = 0.30

YOON = [
    # k
    "きゃ", "きゅ", "きょ",
    # s/sh
    "しゃ", "しゅ", "しょ",
    # ch
    "ちゃ", "ちゅ", "ちょ",
    # n
    "にゃ", "にゅ", "にょ",
    # h
    "ひゃ", "ひゅ", "ひょ",
    # m
    "みゃ", "みゅ", "みょ",
    # r
    "りゃ", "りゅ", "りょ",
    # g (dakuten)
    "ぎゃ", "ぎゅ", "ぎょ",
    # j (dakuten)
    "じゃ", "じゅ", "じょ",
    # b (dakuten)
    "びゃ", "びゅ", "びょ",
    # p (handakuten)
    "ぴゃ", "ぴゅ", "ぴょ",
]


def primary_hash(text: str, lang: str = "ja") -> str:
    return hashlib.sha256(f"{lang}:{text}".encode("utf-8")).hexdigest()[:16]


async def synth(text: str, out: Path, attempts: int = 5) -> None:
    """Synthesize with simple exponential backoff on 503/rate-limit errors."""
    import edge_tts  # noqa: PLC0415
    last: Exception | None = None
    for i in range(attempts):
        try:
            comm = edge_tts.Communicate(text, voice=VOICE)
            await comm.save(str(out))
            return
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(2 ** i)  # 1, 2, 4, 8, 16
    raise last  # type: ignore[misc]


def pad(src: Path, dst: Path) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-af", f"apad=pad_dur={TRAILING_SILENCE}",
         "-c:a", "libmp3lame", "-q:a", "4", str(dst)],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    seen: set[str] = set()
    if MARKER.exists():
        seen = {l.strip() for l in MARKER.read_text().splitlines() if l.strip()}

    print(f"Generating {len(YOON)} yōon (Nanami, {{k}}。 baseline)")
    written = 0
    for kana in YOON:
        h = primary_hash(kana)
        out = TTS_DIR / "ja" / f"{h}.mp3"
        rel = f"ja/{h}.mp3"
        manifest_path = f"tts/ja/{h}.mp3"
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tmp = Path(tf.name)
        try:
            asyncio.run(synth(f"{kana}。", tmp))
            if pad(tmp, out):
                written += 1
                seen.add(rel)
            else:
                print(f"  FAIL {kana}", file=sys.stderr)
        finally:
            if tmp.exists():
                tmp.unlink()
        manifest[f"ja:{kana}"] = [manifest_path]

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    MARKER.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")
    print(f"Done. wrote={written}/{len(YOON)} files; manifest updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
