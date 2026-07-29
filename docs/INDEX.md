# INDEX.md — research map (lingo-core)

**Status:** LIVE · built 2026-07-26 · maintained by the daily session-start index job. Frontend map: `../../lingo/docs/INDEX.md`.

## Where to look

| Question | Go to |
|---|---|
| Architecture / conformance gaps | `ARCHITECTURE_REVIEW.md` (read before structural changes) |
| Auth deps, repo pattern, XP/quests/streak invariants, dev loop (uv!) | `CLAUDE.md` |
| TTS pipeline | `TTS.md` + `scripts/tts/` (generate, whisper_audit, regen_best, gen_dialogue_voices, pad_silence…) |
| Progress rollup, deck storage decisions | `adr/0001-progress-api-hybrid-rollup.md`, `adr/0002-deck-content-storage-and-versioning.md` |
| Unbuilt forward specs | `cosmetics-design-2026-05-25.md`, `leagues-design-2026-05-25.md` (mock-only) |
| Test fixtures | `../test_decks/README.md` |

## Landmines

- `xp-curve-design-2026-05-25.md` is STALE — XP is server-authoritative via `XpEconomyConfig` (`app/platform_settings/schemas.py`), not the client curve it describes.
- Known-broken/missing (per CLAUDE.md, still true): community persistence = in-memory mock on ALL backends; stories have no Dynamo repo; `is_admin()` not enforced — admin routes open.
- CI = ruff + pytest only; no e2e.
- Venv is uv-managed, no pip; binaries not on PATH — call `.venv/bin/*` directly.
