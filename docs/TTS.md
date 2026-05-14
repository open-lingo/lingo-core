# TTS — generation pipeline

Pre-generates MP3 audio for lesson content using **Microsoft Edge-TTS**
(free cloud API, no key required). Output lands in the frontend `publicDir`
so Vite serves it as a static asset during development. A second provider,
**Kokoro** (local, offline, CPU-real-time), is wired up but not the
production choice — Edge wins on quality.

This is the *generation-only* slice. The future `/api/tts` route described
in [lingo/docs/TTS_PLANNING.md](../../lingo/docs/TTS_PLANNING.md) reuses
the same hash key + path layout, so swapping to a server-side cache later
is mechanical.

## Status

| Provider | Languages / voices | Quality | Use |
|---|---|---|---|
| **Edge-TTS** (default) | `ja`: `ja-JP-NanamiNeural` (F), `ja-JP-KeitaNeural` (M) | High | Production audio |
| Kokoro 0.9.4 | `ja`: 5 voices (`jf_alpha`, `jf_gongitsune`, `jf_nezumi`, `jf_tebukuro`, `jm_kumo`) | Medium (vocaloid-like) | Local fallback / no-network case |

Edge-TTS only ships **two** Japanese voices — the rest (Aoi, Daichi, Mayu,
etc.) were retired and are now Azure-only (paid). Re-check via:

```bash
.venv-tts/bin/python -c "import asyncio,edge_tts; \
  print([v['ShortName'] for v in asyncio.run(edge_tts.list_voices()) if v['Locale'].startswith('ja')])"
```

Korean and other languages are not implemented yet (no current need; Edge
covers Korean if/when added — voices like `ko-KR-SunHiNeural`).

## Content strategy — Duolingo-style multi-voice

**Goal: every word, sentence, and listening prompt in the content library
should exist in *every* available voice for the target language.** Then at
runtime the player picks a voice — randomly per session, or sticky per
"speaker" in dialogues, or matched to the speaker's gender in a story.
That's the Duolingo pattern: same content, voice variety provides freshness
and trains the learner's ear on more than one speaker.

For Japanese on Edge-TTS that's `text × 2 voices` files per phrase
(Nanami + Keita). When more voices land (Style-Bert-VITS2 add, Edge adds
back retired voices, etc.) the catalog regenerates against the new voice
set automatically.

**Status of this in the current implementation: NOT yet wired.** Today
each phrase generates one mp3 with the language's `default_voice`. To move
to the multi-voice catalog the generator needs:

1. Cache key changes from `lang:text` → `lang:voice:text`.
2. Hash16 / output path become `tts/{lang}/{voice}/{hash}.mp3` (or fold
   `voice` into the filename — either is fine, voice-as-dir is easier to
   `ls`).
3. Manifest restructures from `{key: relPath}` to nested
   `{lang:text: {voice: relPath, ...}}` so the frontend can enumerate the
   voice options for a given phrase.
4. `generate_content` iterates `provider.SAMPLE_VOICES[lang]` (or a new
   `PRODUCTION_VOICES[lang]` list — `SAMPLE_VOICES` doubles for now)
   instead of using a single `default_voice`.
5. The tester page's "Generated content audio" table grows a column per
   voice (or one row per `(text, voice)` pair).

None of these are large changes — call ~half a day of work — but they're
not in this commit because we wanted the audio quality decision settled
first. **Do this before authoring the first real Japanese deck**, not
after, so the migration is to a fresh corpus instead of regenerating
hundreds of files.

## One-time install

```bash
cd lingo-core
python3 -m venv .venv-tts            # uses /usr/bin/python3 (3.10 on this box)
.venv-tts/bin/python -m pip install --upgrade pip
.venv-tts/bin/python -m pip install edge-tts soundfile

# Optional — only needed if you want to run the local Kokoro provider too:
.venv-tts/bin/python -m pip install kokoro misaki[ja] numpy
.venv-tts/bin/python -m unidic download   # ~526 MB Japanese dictionary
```

Always invoke pip via `python -m pip` — the `.venv-tts/bin/pip` shebang is
locked to a path the venv hasn't moved to. (If you rebuild the venv from
scratch it'll be fine; this is just an artifact of the rename from `.venv`
to `.venv-tts`.)

The `.venv-tts` is intentionally separate from any future `.venv` for the
backend app: the app needs Python ≥3.13, while torch/Kokoro install most
cleanly against the system Python (3.10). Edge-TTS works fine on either.

## Generating audio

From `lingo-core/`:

```bash
# See what would be generated, no model load (works for either provider)
.venv-tts/bin/python -m scripts.tts.generate --dry-run

# Generate everything not yet cached, default provider = edge
.venv-tts/bin/python -m scripts.tts.generate

# Force regeneration of every file (e.g., after switching voices)
.venv-tts/bin/python -m scripts.tts.generate --force

# Use Kokoro (local) instead
.venv-tts/bin/python -m scripts.tts.generate --provider kokoro

# Use a specific voice for the run (overrides LANG_CONFIG default)
.venv-tts/bin/python -m scripts.tts.generate --voice ja-JP-KeitaNeural

# One language only
.venv-tts/bin/python -m scripts.tts.generate --lang ja

# Stop after N files (debugging)
.venv-tts/bin/python -m scripts.tts.generate --limit 3

# Voice-comparison samples for the tester page (one phrase × every voice)
.venv-tts/bin/python -m scripts.tts.generate --samples --all-providers
.venv-tts/bin/python -m scripts.tts.generate --samples --provider edge   # one provider
```

Performance reference (Edge-TTS, single phrase): ~0.5–1.0s per phrase, all
network-bound. 10 short phrases ~6 seconds end-to-end.

> **NOTE — current single-voice limitation.** This `generate_content`
> path uses one voice per language per run. The Duolingo-style multi-voice
> catalog described above is the next planned change; see "Content
> strategy" for the steps.

## Inputs

The script enumerates Japanese text from one source today:

- **Deck card fronts** — every `cards[].front` field in
  `lingo-core/test_decks/*.json` files where `languageId == "ja"`.

It de-duplicates by `(lang, text)` so the same word across decks generates
only once.

When Japanese mock lessons land, add a second collector that walks
`lingo/src/features/lesson/data/mock-*.ts` and pulls Japanese text from:

- `teach.vocab.term`
- `speaking.targetPhrase`
- `listening_comprehension.transcript`
- `listening_build.targetSentence`
- `build_sentence.targetSentence`
- `multiple_choice.prompt` (when the prompt is in the target language)
- `translate.acceptedAnswers[]` (when source is `native`)

The current mock lessons are Korean (`mock-m1-l1.ts`, `mock-m1-l2.ts`) so
this collector has nothing to do until ja lessons exist.

## Outputs

Current single-voice layout (changes when multi-voice lands):

```
lingo/src/pub/tts/
├── manifest.json                       # {"ja:こんにちは": "tts/ja/c34e...mp3", ...}
├── ja/
│   ├── c34e1a1b60652761.mp3            # ja:こんにちは (current default voice)
│   └── ...
└── samples/                            # voice-comparison files for tts-tester.html
    ├── manifest.json                   # {"edge": {"ja": {phrase, voices}}, "kokoro": {...}}
    ├── edge/ja/ja-JP-NanamiNeural.mp3
    ├── edge/ja/ja-JP-KeitaNeural.mp3
    └── kokoro/ja/jf_alpha.mp3 ...
```

`src/pub/` is the project's Vite `publicDir` (see `lingo/vite.config.ts`),
so an mp3 at `lingo/src/pub/tts/ja/c34e1a1b60652761.mp3` is served at
`http://localhost:5173/tts/ja/c34e1a1b60652761.mp3`.

### Hash format

```python
hash16 = sha256(f"{lang}:{text}".encode("utf-8")).hexdigest()[:16]
out    = f"tts/{lang}/{hash16}.mp3"
```

This matches the cache layout in `lingo/docs/TTS_PLANNING.md` step 2 (the
keys differ only by truncation length — easy to extend to full sha256
later if collisions ever matter; 16 hex chars = 64 bits is fine for this
scale). When multi-voice lands, the input becomes `f"{lang}:{voice}:{text}"`
and `voice` is added to the path as well.

### Manifest

`manifest.json` is the `cache_key → relative path` map, sorted for
deterministic diffs:

```json
{
  "ja:こんにちは": "tts/ja/c34e1a1b60652761.mp3",
  "ja:私は学生です": "tts/ja/883baa966b40913d.mp3"
}
```

Multi-voice future shape:

```json
{
  "ja:こんにちは": {
    "ja-JP-NanamiNeural": "tts/ja/ja-JP-NanamiNeural/c34e1a1b60652761.mp3",
    "ja-JP-KeitaNeural":  "tts/ja/ja-JP-KeitaNeural/c34e1a1b60652761.mp3"
  }
}
```

Frontend resolution (proposed, not yet implemented):

```ts
import manifest from "/tts/manifest.json";

// single-voice (current)
export function getTtsUrl(text: string, lang: string): string | null {
  const path = manifest[`${lang}:${text}`];
  return path ? `/${path}` : null;
}

// multi-voice (future) — pick a random voice, or accept one explicitly
export function getTtsUrl(text: string, lang: string, voice?: string): string | null {
  const voices = manifest[`${lang}:${text}`];
  if (!voices) return null;
  const pick = voice ?? randomChoice(Object.keys(voices));
  return voices[pick] ? `/${voices[pick]}` : null;
}
```

That resolver is intentionally not added in this change — the parallel
frontend agent owns UI wiring.

## Voice selection

Voice config lives in each provider's `LANG_CONFIG` and `SAMPLE_VOICES`
dicts at the top of `scripts/tts/generate.py`:

| Provider | Lang | Default voice | All voices |
|---|---|---|---|
| `edge` | `ja` | `ja-JP-NanamiNeural` (F) | + `ja-JP-KeitaNeural` (M) |
| `kokoro` | `ja` | `jf_alpha` (F) | + `jf_gongitsune`, `jf_nezumi`, `jf_tebukuro`, `jm_kumo` |

Switching the **single-voice** default is a regenerate-everything
operation. The future multi-voice catalog removes that wart entirely —
every voice gets generated upfront.

## Committing audio files

The generated mp3s are tiny (~3–13 KB each at 64 kbps mono 24 kHz from
Kokoro; ~5–32 KB at 48 kbps from Edge-TTS) and per the session decision
are **committed to the repo**:

- Reviewable diff per lesson — added phrases show up as added files.
- Every dev / CI gets identical audio, no environment drift.
- No CDN/S3 needed yet.

At ~10–20 KB per voice per phrase, the multi-voice corpus is
roughly `2 × phrases × 15 KB`. 1000 phrases × 2 ja voices ≈ 30 MB. Still
fine. If we eventually expand to 5+ voices and 5000+ phrases that's the
trigger to swap committed files for the planned `/api/tts` route with
S3-backed cache.

## Migration path to /api/tts

When ready to flip the server-side switch:

1. Add `app/tts/router.py` exposing `GET /api/tts?text=...&lang=...&voice=...`.
2. Reuse the hash function and the `tts/{lang}/[{voice}/]{hash}.mp3`
   layout, but persist to S3 instead of `lingo/src/pub/`.
3. Frontend resolver checks the manifest first (fast static path); on
   miss, calls the API which generates + uploads + returns the CDN URL.
4. Eventually retire the manifest in favor of API-only resolution; keep
   the hash format identical so committed mp3s remain valid keys.

The provider abstraction in `TTS_PLANNING.md` step 1 already exists in
`scripts/tts/generate.py` — `EdgeTtsProvider` and `KokoroProvider`
implement a `TtsProvider` base. Adding `ElevenLabsProvider`,
`StyleBertVITS2Provider`, etc. is just a new subclass registered in
`PROVIDERS`.
