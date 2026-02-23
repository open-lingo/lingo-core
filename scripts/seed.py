"""Seed the local SQLite database with test data.

Usage:
    python -m scripts.seed            # seed (skip existing)
    python -m scripts.seed --reset    # wipe everything and re-seed
"""

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime

import aiosqlite

from app.config import settings

DEV_USER = settings.DEV_USER

SEED_USERS = [
    {
        "auth0_id": DEV_USER,
        "username": "trevor",
        "display_name": "Trevor",
        "profile_picture_key": None,
        "status": "active",
    },
    {
        "auth0_id": "dev|user-2",
        "username": "hana",
        "display_name": "Hana Kim",
        "profile_picture_key": None,
        "status": "active",
    },
    {
        "auth0_id": "dev|user-3",
        "username": "testuser",
        "display_name": "Test User",
        "profile_picture_key": None,
        "status": "active",
    },
]

SEED_SETTINGS = {
    DEV_USER: {"theme": "dark", "learningLanguage": "ko", "uiLocale": "en"},
    "dev|user-2": {"theme": "light", "learningLanguage": "en", "uiLocale": "ko"},
    "dev|user-3": {"theme": "system", "learningLanguage": "ko", "uiLocale": "en"},
}

# Deck seed data: manifest + cards. Matches docs/dataformats/flashcards/ examples.
SEED_DECKS = [
    {
        "id": "ko-beginner",
        "manifest": {
            "languageId": "ko",
            "name": "Korean beginner",
            "courseId": "mock-1",
            "version": "1.0",
            "locale": "en",
        },
        "cards": [
            {
                "id": "ko-1",
                "front": "안녕하세요",
                "back": "Hello / Good day",
                "note": "Polite greeting.",
                "image": "https://open-lingo-content.s3.example.com/ko/greetings-wave.jpg",
                "type": "word",
                "reasoning": "안녕 = peace/wellness, 하다 = do, 세요 = polite ending. Literally 'do peace' → hello.",
                "parts": [
                    {"segment": "안녕", "meaning": "peace, wellness"},
                    {"segment": "하", "meaning": "do (stem)"},
                    {"segment": "세요", "particleId": "세요"},
                ],
            },
            {
                "id": "ko-2",
                "front": "저는 학생입니다",
                "back": "I am a student.",
                "type": "sentence",
                "reasoning": "저 = I (humble), 는 = topic, 학생 = student, 이다 = to be, ㅂ니다 = formal declarative.",
                "words": [
                    {"segment": "저", "meaning": "I (humble)"},
                    {"segment": "는", "particleId": "은_는"},
                    {"segment": "학생", "meaning": "student"},
                    {"segment": "입니다", "meaning": "am (formal)"},
                ],
            },
            {
                "id": "ko-3",
                "front": "사과를 먹어요",
                "back": "I eat an apple. / (Someone) eats an apple.",
                "type": "sentence",
                "reasoning": "사과 = apple, 를 = object marker, 먹다 = eat, 어요 = polite present.",
                "words": [
                    {"segment": "사과", "meaning": "apple"},
                    {"segment": "를", "particleId": "을_를"},
                    {"segment": "먹어요", "meaning": "eat (polite)"},
                ],
            },
            {
                "id": "ko-4",
                "front": "학교에 가요",
                "back": "I go to school.",
                "type": "sentence",
                "reasoning": "학교 = school, 에 = to (place), 가다 = go, 아요/어요 = polite.",
                "words": [
                    {"segment": "학교", "meaning": "school"},
                    {"segment": "에", "particleId": "에"},
                    {"segment": "가요", "meaning": "go (polite)"},
                ],
            },
            {
                "id": "ko-5",
                "front": "감사합니다",
                "back": "Thank you.",
                "note": "Formal thanks.",
                "type": "other",
                "definition": "Thank you (formal).",
                "context": "Use after someone helps you or gives something.",
                "reasoning": "감사 = gratitude, 하다 = do, ㅂ니다 = formal. 'I do gratitude.'",
            },
        ],
    },
    {
        "id": "addon-kdrama",
        "manifest": {
            "languageId": "ko",
            "name": "K-Drama Phrases",
            "courseId": None,
            "version": "1.0",
            "image": "https://picsum.photos/seed/kdrama/400/200",
            "locale": "en",
        },
        "cards": [
            {
                "id": "kdrama-1",
                "front": "뭐 해요?",
                "back": "What are you doing?",
                "type": "sentence",
                "note": "Casual, common in dramas.",
                "words": [
                    {"segment": "뭐", "meaning": "what"},
                    {"segment": "해요", "meaning": "do (polite)"},
                ],
            },
            {
                "id": "kdrama-2",
                "front": "진짜요?",
                "back": "Really?",
                "type": "word",
                "note": "Very common reaction.",
                "image": "https://user-images.githubusercontent.com/example/surprised-face.png",
                "parts": [
                    {"segment": "진짜", "meaning": "really"},
                    {"segment": "요", "meaning": "polite ending"},
                ],
            },
            {
                "id": "kdrama-3",
                "front": "잘 지냈어요?",
                "back": "How have you been?",
                "type": "sentence",
                "note": "Common greeting in dramas.",
                "words": [
                    {"segment": "잘", "meaning": "well"},
                    {"segment": "지냈어요", "meaning": "spent time (past polite)"},
                ],
            },
            {
                "id": "kdrama-4",
                "front": "다음에 봐요",
                "back": "See you next time.",
                "type": "sentence",
                "words": [
                    {"segment": "다음에", "meaning": "next time"},
                    {"segment": "봐요", "meaning": "see (polite)"},
                ],
            },
            {
                "id": "kdrama-5",
                "front": "알겠어요",
                "back": "I understand. / Got it.",
                "type": "word",
                "note": "Polite acknowledgment.",
                "parts": [
                    {"segment": "알", "meaning": "know"},
                    {"segment": "겠어요", "meaning": "will (polite)"},
                ],
            },
        ],
    },
    {
        "id": "ja-beginner",
        "manifest": {
            "languageId": "ja",
            "name": "Japanese beginner",
            "courseId": "mock-1",
            "version": "1.0",
            "locale": "en",
        },
        "cards": [
            {
                "id": "ja-1",
                "front": "こんにちは",
                "back": "Hello / Good afternoon",
                "note": "Standard daytime greeting.",
                "type": "word",
                "reasoning": "こんにち = this day, は = topic particle. Literally 'as for today' → hello.",
                "parts": [
                    {"segment": "こんにち", "meaning": "this day"},
                    {"segment": "は", "particleId": "wa"},
                ],
            },
            {
                "id": "ja-2",
                "front": "私は学生です",
                "back": "I am a student.",
                "type": "sentence",
                "reasoning": "私 = I, は = topic, 学生 = student, です = polite copula.",
                "words": [
                    {"segment": "私", "meaning": "I"},
                    {"segment": "は", "particleId": "wa"},
                    {"segment": "学生", "meaning": "student"},
                    {"segment": "です", "meaning": "am/is (polite)"},
                ],
            },
            {
                "id": "ja-3",
                "front": "りんごを食べます",
                "back": "I eat an apple.",
                "type": "sentence",
                "reasoning": "りんご = apple, を = object marker, 食べます = eat (polite).",
                "words": [
                    {"segment": "りんご", "meaning": "apple"},
                    {"segment": "を", "particleId": "wo"},
                    {"segment": "食べます", "meaning": "eat (polite)"},
                ],
            },
            {
                "id": "ja-4",
                "front": "学校に行きます",
                "back": "I go to school.",
                "type": "sentence",
                "reasoning": "学校 = school, に = to (direction), 行きます = go (polite).",
                "words": [
                    {"segment": "学校", "meaning": "school"},
                    {"segment": "に", "particleId": "ni"},
                    {"segment": "行きます", "meaning": "go (polite)"},
                ],
            },
            {
                "id": "ja-5",
                "front": "ありがとうございます",
                "back": "Thank you (polite).",
                "note": "Polite thanks, used in most situations.",
                "type": "other",
                "definition": "Thank you (polite).",
                "context": "Use after someone helps you or gives something.",
                "reasoning": "ありがたい = grateful, ございます = polite form of ある (to exist).",
            },
        ],
    },
    {
        "id": "addon-particles",
        "manifest": {
            "languageId": "ko",
            "name": "Korean Particles Master",
            "courseId": None,
            "version": "1.0",
            "locale": "en",
        },
        "cards": [
            {
                "id": "part-1",
                "front": "은/는",
                "back": "Topic marker",
                "type": "word",
                "note": "은 after consonant, 는 after vowel.",
                "parts": [{"segment": "은/는", "meaning": "topic marker"}],
            },
            {
                "id": "part-2",
                "front": "이/가",
                "back": "Subject marker",
                "type": "word",
                "note": "이 after consonant, 가 after vowel.",
                "parts": [{"segment": "이/가", "meaning": "subject marker"}],
            },
            {
                "id": "part-3",
                "front": "을/를",
                "back": "Object marker",
                "type": "word",
                "note": "을 after consonant, 를 after vowel.",
                "parts": [{"segment": "을/를", "meaning": "object marker"}],
            },
            {
                "id": "part-4",
                "front": "에",
                "back": "At / to / in (time or place)",
                "type": "word",
                "parts": [{"segment": "에", "meaning": "at, to, in"}],
            },
            {
                "id": "part-5",
                "front": "의",
                "back": "Possessive (ʼs, of)",
                "type": "word",
                "parts": [{"segment": "의", "meaning": "possessive"}],
            },
        ],
    },
    {
        "id": "addon-jlpt-n5",
        "manifest": {
            "languageId": "ja",
            "name": "JLPT N5 Vocab",
            "courseId": None,
            "version": "1.0",
            "locale": "en",
        },
        "cards": [
            {
                "id": "n5-1",
                "front": "人",
                "back": "Person, people",
                "type": "word",
                "note": "Kun: ひと, On: ジン, ニン",
                "parts": [{"segment": "人", "meaning": "person"}],
            },
            {
                "id": "n5-2",
                "front": "日",
                "back": "Day, sun",
                "type": "word",
                "note": "Kun: ひ, か, On: ニチ, ジツ",
                "parts": [{"segment": "日", "meaning": "day, sun"}],
            },
            {
                "id": "n5-3",
                "front": "水",
                "back": "Water",
                "type": "word",
                "note": "Kun: みず, On: スイ",
                "parts": [{"segment": "水", "meaning": "water"}],
            },
            {
                "id": "n5-4",
                "front": "食べる",
                "back": "To eat",
                "type": "word",
                "note": "Ichidan verb.",
                "parts": [{"segment": "食べる", "meaning": "to eat"}],
            },
            {
                "id": "n5-5",
                "front": "大きい",
                "back": "Big, large",
                "type": "word",
                "note": "い-adjective.",
                "parts": [{"segment": "大きい", "meaning": "big"}],
            },
        ],
    },
]

INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    auth0_id            TEXT NOT NULL UNIQUE,
    username            TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    profile_picture_key TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    data    TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS deck_manifests (
    id          TEXT PRIMARY KEY,
    language_id TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    course_id   TEXT,
    author_id   TEXT,
    status      TEXT NOT NULL DEFAULT 'published',
    version     TEXT NOT NULL DEFAULT '1.0',
    card_count  INTEGER NOT NULL DEFAULT 0,
    image       TEXT,
    locale      TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_deck_manifests_language ON deck_manifests (language_id);
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id      TEXT NOT NULL,
    content_type TEXT NOT NULL,
    content_id   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    enabled            INTEGER NOT NULL DEFAULT 1,
    new_cards_per_day  INTEGER NOT NULL DEFAULT 5,
    new_card_order     TEXT NOT NULL DEFAULT 'ordered',
    PRIMARY KEY (user_id, content_type, content_id)
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id);
CREATE TABLE IF NOT EXISTS deck_content (
    deck_id TEXT PRIMARY KEY,
    cards   TEXT NOT NULL
);
"""


async def reset(db: aiosqlite.Connection) -> None:
    print("  Dropping tables...")
    await db.execute("DROP TABLE IF EXISTS subscriptions")
    await db.execute("DROP TABLE IF EXISTS deck_content")
    await db.execute("DROP TABLE IF EXISTS deck_manifests")
    await db.execute("DROP TABLE IF EXISTS user_settings")
    await db.execute("DROP TABLE IF EXISTS users")
    await db.commit()


async def seed(db_path: str, do_reset: bool) -> None:
    print(f"Database: {db_path}")
    db = await aiosqlite.connect(db_path)

    if do_reset:
        await reset(db)

    await db.executescript(INIT_SQL)

    # Migration: user_settings and subscriptions from auth0_id -> user_id (app expects user_id)
    cur = await db.execute("PRAGMA table_info(user_settings)")
    cols = [r[1] for r in await cur.fetchall()]
    if "auth0_id" in cols and "user_id" not in cols:
        await db.execute("DROP TABLE IF EXISTS user_settings")
        await db.execute(
            "CREATE TABLE user_settings (user_id TEXT PRIMARY KEY REFERENCES users(id), data TEXT NOT NULL DEFAULT '{}')"
        )
        for auth0_id, prefs in SEED_SETTINGS.items():
            ucur = await db.execute("SELECT id FROM users WHERE auth0_id = ?", (auth0_id,))
            urow = await ucur.fetchone()
            if urow:
                await db.execute(
                    "INSERT INTO user_settings (user_id, data) VALUES (?, ?)",
                    (urow[0], json.dumps(prefs)),
                )
        await db.commit()
    cur = await db.execute("PRAGMA table_info(subscriptions)")
    sub_cols = [r[1] for r in await cur.fetchall()]
    if "auth0_id" in sub_cols and "user_id" not in sub_cols:
        await db.execute("DROP TABLE IF EXISTS subscriptions")
        await db.execute("""
            CREATE TABLE subscriptions (
                user_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                content_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                new_cards_per_day INTEGER NOT NULL DEFAULT 5,
                new_card_order TEXT NOT NULL DEFAULT 'ordered',
                PRIMARY KEY (user_id, content_type, content_id)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id)")
        await db.commit()

    # Migration: ensure deck_manifests has status, description, author_id (for older DBs)
    for col, col_def in [
        ("description", "TEXT"),
        ("author_id", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'published'"),
    ]:
        try:
            await db.execute(f"ALTER TABLE deck_manifests ADD COLUMN {col} {col_def}")
            await db.commit()
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

    # Ensure seed decks are published (fix existing DBs where they were inserted as draft)
    seed_ids = [d["id"] for d in SEED_DECKS]
    placeholders = ",".join("?" * len(seed_ids))
    await db.execute(
        f"UPDATE deck_manifests SET status = 'published' WHERE id IN ({placeholders})",
        seed_ids,
    )
    await db.commit()

    now = datetime.now(UTC).isoformat()
    created = 0
    skipped = 0

    auth0_to_user_id: dict[str, str] = {}
    for u in SEED_USERS:
        cur = await db.execute("SELECT id FROM users WHERE auth0_id = ?", (u["auth0_id"],))
        row = await cur.fetchone()
        if row:
            auth0_to_user_id[u["auth0_id"]] = row[0]
            skipped += 1
            continue

        user_id = str(uuid.uuid4())
        auth0_to_user_id[u["auth0_id"]] = user_id
        await db.execute(
            """INSERT INTO users (id, auth0_id, username, display_name,
                                  profile_picture_key, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                u["auth0_id"],
                u["username"],
                u["display_name"],
                u["profile_picture_key"],
                u["status"],
                now,
                now,
            ),
        )
        created += 1

    settings_created = 0
    for auth0_id, prefs in SEED_SETTINGS.items():
        user_id = auth0_to_user_id.get(auth0_id)
        if not user_id:
            continue
        cur = await db.execute("SELECT 1 FROM user_settings WHERE user_id = ?", (user_id,))
        if await cur.fetchone():
            continue
        await db.execute(
            "INSERT INTO user_settings (user_id, data) VALUES (?, ?)",
            (user_id, json.dumps(prefs)),
        )
        settings_created += 1

    deck_created = 0
    for deck in SEED_DECKS:
        cur = await db.execute("SELECT 1 FROM deck_manifests WHERE id = ?", (deck["id"],))
        if await cur.fetchone():
            continue
        manifest = deck["manifest"]
        cards = deck["cards"]
        # Use status='published' so decks appear in community browse (listAdminDecks filters by published)
        await db.execute(
            """INSERT INTO deck_manifests
                   (id, language_id, name, description, course_id, author_id, status, version, card_count, image, locale, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                deck["id"],
                manifest["languageId"],
                manifest["name"],
                manifest.get("description"),
                manifest.get("courseId"),
                manifest.get("authorId"),
                "published",
                manifest.get("version", "1.0"),
                len(cards),
                manifest.get("image"),
                manifest.get("locale"),
                now,
                now,
            ),
        )
        await db.execute(
            "INSERT INTO deck_content (deck_id, cards) VALUES (?, ?)",
            (deck["id"], json.dumps(cards)),
        )
        deck_created += 1

    await db.commit()
    await db.close()

    print(f"  Users:    {created} created, {skipped} skipped (already exist)")
    print(f"  Settings: {settings_created} created")
    print(f"  Decks:    {deck_created} created")
    print("Done.")


if __name__ == "__main__":
    do_reset = "--reset" in sys.argv
    asyncio.run(seed(settings.SQLITE_PATH, do_reset))
