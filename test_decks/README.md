# Test decks

Deck JSON files for testing the DynamoDB (or other backend) implementation. These match the deck content that would normally be seeded into SQLite.

Use the **Upload deck** feature in the app (Community → Contribute → Create → Upload deck) to load these into your environment. No database seeding required.

## Format

Each file follows the `DeckCreate` API format:

- `languageId` (required)
- `name` (required)
- `description` (optional)
- `image` (optional)
- `cards` (required) — array of cards with `front`, `back`, `type` (word|sentence|other)

## Decks

| File | Name | Language | Cards |
|------|------|----------|-------|
| `ko-beginner.json` | Korean beginner | ko | 5 |
| `k-drama-phrases.json` | K-Drama Phrases | ko | 5 |
| `korean-particles-master.json` | Korean Particles Master | ko | 5 |
| `ja-beginner.json` | Japanese beginner | ja | 5 |
| `jlpt-n5-vocab.json` | JLPT N5 Vocab | ja | 5 |
