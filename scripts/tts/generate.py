"""Generate TTS audio for lesson content.

Supports multiple providers behind a uniform interface so we can A/B them:

- `kokoro` — local, CPU-real-time, 82M params. Tiny + fast, quality is "decent".
- `edge`   — Microsoft Edge-TTS cloud API, free, no API key. Quality jump
             over Kokoro at the cost of needing internet at generation time.

Both write to the same on-disk layout so swapping providers per lesson is
just a regenerate. The `--samples` mode emits one fixed phrase per voice
into `tts/samples/<provider>/<lang>/<voice>.mp3` for side-by-side A/B
comparison via the `tts-tester.html` page.

Usage:
    .venv-tts/bin/python -m scripts.tts.generate
    .venv-tts/bin/python -m scripts.tts.generate --provider edge
    .venv-tts/bin/python -m scripts.tts.generate --samples            # all providers
    .venv-tts/bin/python -m scripts.tts.generate --samples --provider edge
    .venv-tts/bin/python -m scripts.tts.generate --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parents[3]   # /home/beast/projects/lingle
LINGO_CORE = REPO_ROOT / "lingo-core"
LINGO_FE   = REPO_ROOT / "lingo"

DECKS_DIR  = LINGO_CORE / "test_decks"
OUT_DIR    = LINGO_FE / "src" / "pub" / "tts"
MANIFEST   = OUT_DIR / "manifest.json"

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class TtsProvider:
    """Synthesize one (text, lang, voice) → write mp3 to `out_path`.

    Subclasses keep their own one-time setup state (loaded model,
    open client, etc.). `synthesize` is called once per text and is
    expected to be blocking — the caller serializes batch generation.
    """

    name: str = "?"

    def synthesize(self, text: str, lang: str, voice: str, out_path: Path) -> float:
        """Returns the duration in seconds of the generated audio."""
        raise NotImplementedError


class KokoroProvider(TtsProvider):
    name = "kokoro"

    # Per-language Kokoro setup. lang_code is the single-char code
    # KPipeline wants ("j" for Japanese, "a" for en-US, etc.).
    LANG_CONFIG: dict[str, dict[str, str]] = {
        "ja": {"kokoro_lang_code": "j", "default_voice": "jf_alpha"},
    }

    SAMPLE_VOICES: dict[str, list[str]] = {
        "ja": ["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo"],
    }

    def __init__(self) -> None:
        # Lazy import — torch + kokoro are heavy.
        from kokoro import KPipeline as _KPipeline  # noqa: PLC0415
        self._KPipeline = _KPipeline
        self._pipes: dict[str, object] = {}

    def _pipe(self, lang: str):
        if lang not in self._pipes:
            cfg = self.LANG_CONFIG[lang]
            self._pipes[lang] = self._KPipeline(
                lang_code=cfg["kokoro_lang_code"],
                repo_id="hexgrad/Kokoro-82M",
            )
        return self._pipes[lang]

    def synthesize(self, text: str, lang: str, voice: str, out_path: Path) -> float:
        import numpy as np  # noqa: PLC0415
        import soundfile as sf  # noqa: PLC0415
        pipe = self._pipe(lang)
        chunks = []
        for result in pipe(text, voice=voice):
            a = result.audio.numpy() if hasattr(result.audio, "numpy") else np.asarray(result.audio)
            chunks.append(a)
        if not chunks:
            raise RuntimeError("Kokoro produced no audio chunks")
        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        sf.write(out_path, audio, 24000, format="MP3")
        return len(audio) / 24000


class EdgeTtsProvider(TtsProvider):
    name = "edge"

    LANG_CONFIG: dict[str, dict[str, str]] = {
        "ja": {"default_voice": "ja-JP-NanamiNeural"},
    }

    # Edge-TTS currently exposes only two ja voices (Microsoft retired the
    # rest; Aoi/Daichi/Mayu are Azure-only). Verify via:
    #   .venv-tts/bin/python -c "import asyncio,edge_tts; \
    #     print(asyncio.run(edge_tts.list_voices()))"
    SAMPLE_VOICES: dict[str, list[str]] = {
        "ja": [
            "ja-JP-NanamiNeural",   # Female
            "ja-JP-KeitaNeural",    # Male
        ],
    }

    def __init__(self) -> None:
        import edge_tts  # noqa: PLC0415
        self._edge = edge_tts

    def synthesize(self, text: str, lang: str, voice: str, out_path: Path) -> float:
        async def _gen() -> None:
            comm = self._edge.Communicate(text, voice=voice)
            await comm.save(str(out_path))

        asyncio.run(_gen())
        # Edge-TTS doesn't return audio length directly. Probe via soundfile.
        import soundfile as sf  # noqa: PLC0415
        with sf.SoundFile(str(out_path)) as f:
            return f.frames / f.samplerate


PROVIDERS: dict[str, type[TtsProvider]] = {
    "kokoro": KokoroProvider,
    "edge":   EdgeTtsProvider,
}


# ---------------------------------------------------------------------------
# Shared setup
# ---------------------------------------------------------------------------

SAMPLE_PHRASES: dict[str, str] = {
    "ja": "今日はいい天気ですね。一緒に散歩に行きませんか?",
}


@dataclass(frozen=True)
class Job:
    lang: str
    text: str
    source: str  # "deck:<deckName>:<cardId>" — for logging only

    @property
    def cache_key(self) -> str:
        return f"{self.lang}:{self.text}"

    @property
    def hash16(self) -> str:
        return hashlib.sha256(self.cache_key.encode("utf-8")).hexdigest()[:16]

    @property
    def out_path(self) -> Path:
        return OUT_DIR / self.lang / f"{self.hash16}.mp3"

    @property
    def manifest_value(self) -> str:
        return f"tts/{self.lang}/{self.hash16}.mp3"


def collect_deck_jobs(only_lang: str | None, supported_langs: set[str]) -> list[Job]:
    jobs: list[Job] = []
    for deck_path in sorted(DECKS_DIR.glob("*.json")):
        deck = json.loads(deck_path.read_text(encoding="utf-8"))
        lang = deck.get("languageId")
        if not lang or lang not in supported_langs:
            continue
        if only_lang and lang != only_lang:
            continue
        deck_name = deck.get("name", deck_path.stem)
        for card in deck.get("cards", []):
            front = card.get("front")
            if not isinstance(front, str) or not front.strip():
                continue
            jobs.append(Job(lang=lang, text=front.strip(), source=f"deck:{deck_name}:{card.get('id', '?')}"))
    return jobs


def dedupe_jobs(jobs: list[Job]) -> list[Job]:
    seen: dict[str, Job] = {}
    for job in jobs:
        seen.setdefault(job.cache_key, job)
    return list(seen.values())


def load_manifest() -> dict[str, str]:
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_manifest(manifest: dict[str, str]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def generate_content(provider_name: str, only_lang: str | None, voice: str | None,
                     dry_run: bool, force: bool, limit: int | None) -> int:
    cls = PROVIDERS[provider_name]
    supported = set(cls.LANG_CONFIG)
    jobs = dedupe_jobs(collect_deck_jobs(only_lang=only_lang, supported_langs=supported))
    if not jobs:
        print(f"No jobs found for provider={provider_name}. Check DECKS_DIR and --lang.", file=sys.stderr)
        return 1

    by_lang: dict[str, list[Job]] = {}
    for job in jobs:
        by_lang.setdefault(job.lang, []).append(job)

    print(f"Provider: {provider_name}")
    print(f"Collected {len(jobs)} unique text jobs across {len(by_lang)} language(s):")
    for lang, ljobs in by_lang.items():
        cached = sum(1 for j in ljobs if j.out_path.exists())
        print(f"  {lang}: {len(ljobs)} total, {cached} cached, {len(ljobs) - cached} to generate")

    if dry_run:
        for job in jobs:
            tag = "[cached]" if job.out_path.exists() else "[ new  ]"
            print(f"  {tag} {job.lang} {job.hash16} {job.text!r} ({job.source})")
        return 0

    print(f"Loading provider={provider_name}...")
    t0 = time.perf_counter()
    provider = cls()
    print(f"  ready in {time.perf_counter() - t0:.1f}s")

    manifest = load_manifest()
    written, skipped = 0, 0
    failed: list[tuple[Job, str]] = []

    for lang, ljobs in by_lang.items():
        cfg = cls.LANG_CONFIG[lang]
        v = voice or cfg["default_voice"]
        print(f"\n=== {lang} via {provider_name} voice={v}")
        for i, job in enumerate(ljobs):
            if limit is not None and written >= limit:
                break
            manifest[job.cache_key] = job.manifest_value
            if job.out_path.exists() and not force:
                skipped += 1
                continue
            job.out_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                t1 = time.perf_counter()
                sec = provider.synthesize(job.text, lang, v, job.out_path)
                dt = time.perf_counter() - t1
                print(f"  [{i+1}/{len(ljobs)}] {job.text!r:40} → {job.out_path.name}  "
                      f"({sec:.2f}s in {dt:.2f}s)")
                written += 1
            except Exception as e:  # noqa: BLE001
                failed.append((job, str(e)))
                print(f"  [{i+1}/{len(ljobs)}] FAILED {job.text!r}: {e}", file=sys.stderr)

    save_manifest(manifest)
    print(f"\nDone. wrote={written} cached_skipped={skipped} failed={len(failed)}")
    if failed:
        print("\nFailures:", file=sys.stderr)
        for job, err in failed:
            print(f"  {job.cache_key}  →  {err}", file=sys.stderr)
        return 2
    return 0


def generate_samples(provider_names: list[str], only_lang: str | None, force: bool) -> int:
    """Generate one fixed phrase per voice into tts/samples/<provider>/<lang>/<voice>.mp3.

    Samples manifest:
      lingo/src/pub/tts/samples/manifest.json
        {
          "kokoro": {"ja": {"phrase": "...", "voices": ["jf_alpha", ...]}},
          "edge":   {"ja": {"phrase": "...", "voices": ["ja-JP-NanamiNeural", ...]}}
        }

    Existing entries from other providers are preserved, so partial reruns
    don't wipe the other half of the comparison.
    """
    samples_root = OUT_DIR / "samples"
    samples_root.mkdir(parents=True, exist_ok=True)
    manifest_path = samples_root / "manifest.json"
    samples_manifest: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        try:
            samples_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            samples_manifest = {}

    written, skipped = 0, 0

    for pname in provider_names:
        cls = PROVIDERS[pname]
        provider: TtsProvider | None = None  # lazy — only construct if any work to do
        provider_manifest: dict[str, dict[str, object]] = {}

        for lang, voices in cls.SAMPLE_VOICES.items():
            if only_lang and lang != only_lang:
                continue
            phrase = SAMPLE_PHRASES[lang]
            lang_dir = samples_root / pname / lang
            lang_dir.mkdir(parents=True, exist_ok=True)
            provider_manifest[lang] = {"phrase": phrase, "voices": voices}

            print(f"\n=== samples {pname}/{lang}")
            print(f"  phrase: {phrase!r}")

            for voice in voices:
                out = lang_dir / f"{voice}.mp3"
                if out.exists() and not force:
                    skipped += 1
                    print(f"  [cached] {voice} → {out.name}")
                    continue
                if provider is None:
                    print(f"  Loading provider={pname}...")
                    t0 = time.perf_counter()
                    provider = cls()
                    print(f"    ready in {time.perf_counter() - t0:.1f}s")
                try:
                    t1 = time.perf_counter()
                    sec = provider.synthesize(phrase, lang, voice, out)
                    dt = time.perf_counter() - t1
                    print(f"  [ new  ] {voice} → {out.name}  ({sec:.2f}s in {dt:.2f}s)")
                    written += 1
                except Exception as e:  # noqa: BLE001
                    print(f"  FAILED {voice}: {e}", file=sys.stderr)

        if provider_manifest:
            samples_manifest[pname] = provider_manifest

    manifest_path.write_text(
        json.dumps(samples_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"\nDone. wrote={written} cached_skipped={skipped}")
    print(f"Samples manifest: {manifest_path.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=list(PROVIDERS), default="kokoro",
                    help="Which TTS backend to use (samples mode runs all providers if not set)")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--voice", default=None, help="Override the default voice for this run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--samples", action="store_true",
                    help="Sample-phrase-per-voice mode for tester page A/B comparison")
    ap.add_argument("--all-providers", action="store_true",
                    help="Samples mode only: run every provider in PROVIDERS")
    args = ap.parse_args()

    if args.samples:
        providers = list(PROVIDERS) if args.all_providers else [args.provider]
        return generate_samples(providers, only_lang=args.lang, force=args.force)

    return generate_content(
        provider_name=args.provider,
        only_lang=args.lang,
        voice=args.voice,
        dry_run=args.dry_run,
        force=args.force,
        limit=args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
