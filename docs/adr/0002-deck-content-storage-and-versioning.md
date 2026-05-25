# ADR-0002: Deck content storage tiers & versioning scope

**Status:** Accepted (2026-05-25)  
**Supersedes:** none  
**Related:** `docs/dev/planning/DECK_CONTENT_STORAGE.md` · `app/db/dynamo/deck.py` · `app/db/sqlite/deck.py`

---

## Context

Deck bodies can grow to thousands of cards with rich `parts`/`words` and image URLs. DynamoDB items are capped at **400 KB**. The team also explored multi-version deck snapshots with subscription pins and moderator force-promotion.

---

## Decision

1. **Do not implement in-deck snapshot versioning** (trailing versions, `pinnedVersion`, force promotion) in the near term.
2. **Ship routine updates** as **in-place edits** on a single deck id, with **approval gates** for high churn (workflow later).
3. **Optional future:** new deck id per “edition” with supersede link and subscriber migration UX — separate from snapshot storage.
4. **Implement large-deck support** via **storage tiers**: keep small decks **inline** (current model); move large decks to **object storage (S3)** with a small DB manifest and **ordered `cardOrder` + unordered shard parts**, assembled in **`DeckRepository`** behind unchanged API shapes.

---

## Consequences

- **Positive:** Simpler product model; no version resolver in clients; aligns with infrequent updates on huge decks.
- **Positive:** S3 + manifest scales past Dynamo item limits without a second database.
- **Negative:** Must implement tiering, S3 IAM, and likely editor pagination before very large decks land in prod.
- **Negative:** Edition/migration and approval workflows remain unspecified in code until built.

---

## Current code (not yet changed)

- Dynamo: `cards` JSON on `META` item only.
- SQLite: `deck_content.cards` column only.
- No `storageTier` or S3 deck bucket in Terraform.

---

## Alternatives considered

| Alternative | Outcome |
|-------------|---------|
| In-deck versions + subscription pin + force promotion | Deferred — operational and schema complexity |
| New deck id per version only | Viable for major editions; not default for every edit |
| Dynamo-only sharding (many items per deck) | Possible but costly for full-deck reads; S3 preferred for bulk |
| MongoDB / DocumentDB for card bodies | Rejected for v1 — extra system; S3 sufficient for blob + rare writes |
