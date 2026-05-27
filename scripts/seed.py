"""Seed the local SQLite database with test data.

Usage:
    python -m scripts.seed            # seed (skip existing)
    python -m scripts.seed --reset    # wipe everything and re-seed

Beyond the original users + decks fixtures, this also lays down a fully
populated *social context* so every button on the social page hits real
data: extra users, XP/streak/lingot progress, friendships, friend
requests, blocks, leaderboard rows, activity feed items, reactions,
invite codes/redemptions, and chat threads/messages.

Tables we know exist (from app/db/sqlite/social.py): ``social`` and
``social_leaderboard``. Everything else (activity feed, reactions,
invites, threads, messages, league spotlight, streak snapshot) is
defined as Python constants and seeded into ``CREATE TABLE IF NOT
EXISTS`` tables shaped to match the schemas the parallel backend agent
is shipping. If a column-name mismatch lands, the IF-NOT-EXISTS create
is a no-op against the real table and the inserts use named columns —
adjust here and re-run.
"""

import asyncio
import json
import random
import secrets
import string
import sys
import uuid
from datetime import UTC, datetime, timedelta

import aiosqlite

from app.config import settings

DEV_USER = settings.DEV_USER

# ── Users ───────────────────────────────────────────────────────────────────
# Trevor + the two pre-existing test accounts are the "core" users. The
# extras below give the social page a realistic cast: language-learner-y
# usernames, mixed display names, mixed statuses.

SEED_USERS = [
    {
        "auth0_id": DEV_USER,
        "username": "trevor",
        "display_name": "Trevor",
        "profile_picture_key": None,
        "status": "active",
        "role": "admin",
    },
    {
        "auth0_id": "dev|user-2",
        "username": "hana",
        "display_name": "Hana Kim",
        "profile_picture_key": None,
        "status": "active",
        "role": "user",
    },
    {
        "auth0_id": "dev|user-3",
        "username": "testuser",
        "display_name": "Test User",
        "profile_picture_key": None,
        "status": "active",
        "role": "user",
    },
]

SEED_USERS_EXTRA = [
    # username                  display              status     role    pic
    ("sora_n5", "Sora Tanaka", "active", "user", "avatars/sora.png"),
    ("kenji_dev", "Kenji Watanabe", "active", "user", None),
    ("mai_morning", "Mai Sato", "active", "user", "avatars/mai.png"),
    ("riku2026", "Riku Yamada", "active", "user", None),
    ("aiko_kanji", "Aiko Suzuki", "active", "user", None),
    ("yuto_jpn", "Yuto Nakamura", "active", "user", None),
    ("nori", "Nori Honda", "active", "user", None),
    ("priya_n5", "Priya Iyer", "active", "user", None),
    ("anna_lang", "Anna Schmidt", "active", "user", None),
    ("marcus_ja", "Marcus Reed", "active", "user", None),
    ("luca_eu", "Luca Bianchi", "active", "user", None),
    ("noor_x", "Noor Hassan", "active", "user", None),
    ("elena_lang", "Elena Petrova", "active", "user", None),
    ("diego_es", "Diego Alvarez", "active", "user", None),
    ("rio_jp", "Rio Mori", "active", "user", None),
    ("oldie_old", "Inactive Ian", "inactive", "user", None),
    ("banned_bob", "Banned Bob", "banned", "user", None),
]


def _extra_user_dict(username: str, display: str, status: str, role: str, pic: str | None) -> dict:
    return {
        "auth0_id": f"dev|{username}",
        "username": username,
        "display_name": display,
        "profile_picture_key": pic,
        "status": status,
        "role": role,
    }


for _u in SEED_USERS_EXTRA:
    SEED_USERS.append(_extra_user_dict(*_u))


# Per-user settings. Most extras learn Japanese; a few learn Korean. The
# `social.show_on_leaderboard` flag is opt-in everywhere except Trevor (so
# Trevor's XP writes feed the leaderboard naturally once it's wired).
SEED_SETTINGS: dict[str, dict] = {
    DEV_USER: {
        "theme": "dark",
        "learningLanguage": "ja",
        "uiLocale": "en",
        "learning": {"learningLanguageId": "ja", "uiLocale": "en", "onboardingCompleted": True},
        "social": {
            "visibility": "public",
            "allow_friend_requests": True,
            "show_on_leaderboard": True,
            "show_activity_feed": True,
        },
    },
    "dev|user-2": {
        "theme": "light",
        "learningLanguage": "ja",
        "uiLocale": "ko",
        "learning": {"learningLanguageId": "ja", "uiLocale": "ko", "onboardingCompleted": True},
        "social": {"show_on_leaderboard": True},
    },
    "dev|user-3": {
        "theme": "system",
        "learningLanguage": "ko",
        "uiLocale": "en",
        "learning": {"learningLanguageId": "ko", "uiLocale": "en", "onboardingCompleted": True},
        "social": {"show_on_leaderboard": True},
    },
}

# Default settings for every extra user — pulled from the username/lang hint.
_EXTRA_LANGS = {
    "sora_n5": "ja",
    "kenji_dev": "ja",
    "mai_morning": "ja",
    "riku2026": "ja",
    "aiko_kanji": "ja",
    "yuto_jpn": "ja",
    "nori": "ja",
    "priya_n5": "ja",
    "anna_lang": "ja",
    "marcus_ja": "ja",
    "luca_eu": "es",
    "noor_x": "ja",
    "elena_lang": "es",
    "diego_es": "es",
    "rio_jp": "ja",
    "oldie_old": "ja",
    "banned_bob": "ja",
}
for _name, _lang in _EXTRA_LANGS.items():
    SEED_SETTINGS[f"dev|{_name}"] = {
        "theme": random.Random(_name).choice(["light", "dark", "system", "sepia"]),
        "learningLanguage": _lang,
        "uiLocale": "en",
        "learning": {"learningLanguageId": _lang, "uiLocale": "en", "onboardingCompleted": True},
        "social": {
            "visibility": "public",
            "allow_friend_requests": True,
            "show_on_leaderboard": True,
            "show_activity_feed": True,
        },
    }


# ── Per-user progress (XP / streak / lingots / level / last_active_date) ────
# These get baked into the user row (the parallel ADR-0001 work added these
# columns to the ``users`` table). Hand-tuned spread so the leaderboard has a
# real ranking gradient and the friends-leaderboard isn't all 0s.


def _level_for_xp(xp: int) -> int:
    # Match the rough curve in app/progress/xp.py — 100 XP per level early,
    # ramps up. Cheap approximation suitable for seed data.
    if xp <= 0:
        return 1
    return max(1, int((xp / 100) ** 0.85) + 1)


# (username, xp, streak, weekly_xp, lingots, days_since_last_active)
SEED_PROGRESS: list[tuple[str, int, int, int, int, int]] = [
    # Trevor — mid-pack so he's a believable contender.
    ("trevor", 2480, 8, 940, 320, 0),
    ("hana", 2310, 17, 690, 180, 0),
    ("testuser", 1180, 9, 280, 60, 1),
    # Top tier (great leaderboard candidates)
    ("priya_n5", 7740, 64, 1840, 1820, 0),
    ("kenji_dev", 6890, 47, 1620, 1450, 0),
    ("yuto_jpn", 4920, 38, 1290, 980, 0),
    # Mid
    ("anna_lang", 4120, 23, 1080, 720, 0),
    ("noor_x", 3940, 28, 940, 640, 0),
    ("aiko_kanji", 3210, 30, 820, 510, 1),
    ("mai_morning", 2850, 19, 580, 410, 0),
    # Lower mid
    ("sora_n5", 1850, 12, 420, 240, 0),
    ("marcus_ja", 1430, 11, 280, 160, 2),
    ("luca_eu", 1640, 14, 310, 190, 0),
    ("diego_es", 720, 6, 180, 90, 1),
    # Newish / lapsed
    ("riku2026", 980, 5, 120, 60, 3),
    ("nori", 540, 3, 80, 40, 2),
    ("rio_jp", 580, 4, 100, 50, 2),
    ("elena_lang", 1430, 0, 0, 110, 9),  # streak lapsed
    ("oldie_old", 200, 0, 0, 10, 30),
    ("banned_bob", 50, 0, 0, 0, 14),
]


# ── Friendships ─────────────────────────────────────────────────────────────
# Trevor is friends with 6 — a mix of high-XP and low-XP so the
# friends-leaderboard has a gradient.

TREVOR_FRIENDS = ["kenji_dev", "priya_n5", "mai_morning", "sora_n5", "anna_lang", "riku2026"]

# Other-other friendships so the graph isn't a star.
OTHER_FRIENDSHIPS = [
    ("kenji_dev", "yuto_jpn"),
    ("priya_n5", "noor_x"),
    ("mai_morning", "aiko_kanji"),
    ("anna_lang", "luca_eu"),
    ("sora_n5", "rio_jp"),
    ("yuto_jpn", "noor_x"),
]

# ── Friend requests ─────────────────────────────────────────────────────────
# Three incoming TO Trevor (pending accept/decline), two outgoing FROM Trevor.
INCOMING_REQUESTS_TO_TREVOR = ["aiko_kanji", "marcus_ja", "luca_eu"]
OUTGOING_REQUESTS_FROM_TREVOR = ["nori", "elena_lang"]

# ── Blocks ──────────────────────────────────────────────────────────────────
TREVOR_BLOCKS = ["banned_bob"]

# ── Activity feed ───────────────────────────────────────────────────────────
# Shape mirrors the frontend's MOCK_ACTIVITY: actor + kind + text + reactions.
# We'll seed this into `social_activity` (table will be CREATE IF NOT EXISTS;
# if the parallel agent ships a different schema, the named-column INSERTs
# need updating but the constants stay).
ACTIVITY_KINDS = (
    "lesson_completed",
    "streak_milestone",
    "level_up",
    "friend_joined",
    "achievement",
)

SEED_ACTIVITY: list[dict] = [
    {
        "id": "a-1",
        "actor": "kenji_dev",
        "kind": "lesson_completed",
        "text": "Finished Module 2 — Dakuten & Yōon",
        "days_ago": 0,
        "hours_ago": 12 / 60,
    },
    {
        "id": "a-2",
        "actor": "priya_n5",
        "kind": "streak_milestone",
        "text": "Hit a 64-day streak 🔥",
        "days_ago": 0,
        "hours_ago": 1,
    },
    {
        "id": "a-3",
        "actor": "anna_lang",
        "kind": "achievement",
        "text": "Promoted to Sapphire League",
        "days_ago": 1,
        "hours_ago": 0,
    },
    {
        "id": "a-4",
        "actor": "mai_morning",
        "kind": "achievement",
        "text": "Reached 3,000 XP",
        "days_ago": 2,
        "hours_ago": 0,
    },
    {
        "id": "a-5",
        "actor": "sora_n5",
        "kind": "lesson_completed",
        "text": "Completed M3 Lesson 2 — Particles wa vs ga",
        "days_ago": 0,
        "hours_ago": 3,
    },
    {
        "id": "a-6",
        "actor": "yuto_jpn",
        "kind": "achievement",
        "text": "Mastered 5 new kanji",
        "days_ago": 1,
        "hours_ago": 4,
    },
    {
        "id": "a-7",
        "actor": "trevor",
        "kind": "level_up",
        "text": "Reached Level 7",
        "days_ago": 0,
        "hours_ago": 6,
    },
    {
        "id": "a-8",
        "actor": "aiko_kanji",
        "kind": "streak_milestone",
        "text": "Hit a 30-day streak!",
        "days_ago": 2,
        "hours_ago": 5,
    },
    {
        "id": "a-9",
        "actor": "noor_x",
        "kind": "lesson_completed",
        "text": "Finished M4 Lesson 1 — verb stems",
        "days_ago": 3,
        "hours_ago": 0,
    },
    {
        "id": "a-10",
        "actor": "kenji_dev",
        "kind": "friend_joined",
        "text": "Kenji and Yuto are now friends",
        "days_ago": 4,
        "hours_ago": 1,
    },
    {
        "id": "a-11",
        "actor": "trevor",
        "kind": "lesson_completed",
        "text": "Completed M3 Lesson 3 — building sentences",
        "days_ago": 1,
        "hours_ago": 2,
    },
    {
        "id": "a-12",
        "actor": "priya_n5",
        "kind": "achievement",
        "text": "Mastered 12 vocab cards",
        "days_ago": 5,
        "hours_ago": 0,
    },
    {
        "id": "a-13",
        "actor": "luca_eu",
        "kind": "level_up",
        "text": "Reached Level 4",
        "days_ago": 6,
        "hours_ago": 0,
    },
    {
        "id": "a-14",
        "actor": "anna_lang",
        "kind": "lesson_completed",
        "text": "Finished M2 Lesson 4",
        "days_ago": 5,
        "hours_ago": 3,
    },
    {
        "id": "a-15",
        "actor": "mai_morning",
        "kind": "streak_milestone",
        "text": "Hit a 19-day streak",
        "days_ago": 6,
        "hours_ago": 12,
    },
]

# ── Reactions ───────────────────────────────────────────────────────────────
# (activity_id, reacter_username, kind). Trevor reacts on a couple so
# the UI's `mine: true` flag shows in tests.

REACTION_KINDS = ("wave", "fire", "clap", "target")

SEED_REACTIONS: list[tuple[str, str, str]] = [
    ("a-1", "trevor", "clap"),
    ("a-1", "priya_n5", "wave"),
    ("a-1", "anna_lang", "wave"),
    ("a-1", "mai_morning", "wave"),
    ("a-2", "trevor", "fire"),
    ("a-2", "kenji_dev", "fire"),
    ("a-2", "anna_lang", "fire"),
    ("a-2", "noor_x", "fire"),
    ("a-2", "yuto_jpn", "wave"),
    ("a-3", "priya_n5", "clap"),
    ("a-3", "kenji_dev", "clap"),
    ("a-3", "sora_n5", "target"),
    ("a-4", "anna_lang", "target"),
    ("a-4", "trevor", "target"),
    ("a-5", "kenji_dev", "wave"),
    ("a-6", "priya_n5", "fire"),
    ("a-6", "trevor", "clap"),
    ("a-8", "kenji_dev", "fire"),
    ("a-8", "mai_morning", "fire"),
    ("a-11", "priya_n5", "clap"),
    ("a-11", "kenji_dev", "wave"),
]

# ── Invite code + redemptions ───────────────────────────────────────────────
TREVOR_INVITE_CODE = "INV1A2B3"
SEED_INVITE_REDEMPTIONS: list[dict] = [
    {
        "id": "redeem-1",
        "code": TREVOR_INVITE_CODE,
        "invitee_username": "luca_eu",
        "status": "redeemed",  # graduated — invitee did their first lesson
        "reward_lingots": 100,
        "days_ago": 12,
    },
    {
        "id": "redeem-2",
        "code": TREVOR_INVITE_CODE,
        "invitee_username": "rio_jp",
        "status": "pending",  # signed up, hasn't done a lesson yet
        "reward_lingots": 0,
        "days_ago": 2,
    },
]

# ── Chat threads + messages ─────────────────────────────────────────────────
# Two threads: Trevor ↔ Sora and Trevor ↔ Kenji. Thread #1 has one unread
# from Sora to Trevor so the unread badge in the UI shows.

SEED_THREADS: list[dict] = [
    {
        "id": "thread-sora",
        "user_a": "trevor",
        "user_b": "sora_n5",
        "unread_for_a": 1,  # one unread for Trevor
        "unread_for_b": 0,
    },
    {
        "id": "thread-kenji",
        "user_a": "trevor",
        "user_b": "kenji_dev",
        "unread_for_a": 0,
        "unread_for_b": 0,
    },
]


# ── Quests ──────────────────────────────────────────────────────────────────
# 6 quests for Trevor — 2 daily (one mid-progress, one claimable), 2 weekly
# (one early, one almost done), 1 random (with ~6h expiry), 1 friend quest
# (vs Sora). All progress states are realistic so the UI can show its full
# range without click-through.

SEED_QUESTS: list[dict] = [
    {
        "id": "quest-daily-fifty-xp",
        "owner_username": "trevor",
        "type": "daily",
        "title_key": "quests.daily.fiftyXp.title",
        "description_key": "quests.daily.fiftyXp.desc",
        "emoji": "⚡",
        "progress_current": 30,
        "progress_target": 50,
        "progress_unit": "XP",
        "reward_lingots": 5,
        "reward_xp": 10,
        "reward_ad_free_minutes": 0,
        "reward_streak_shield": False,
        "status": "active",
        "expires_in_hours": 16,
    },
    {
        "id": "quest-daily-flashcards",
        "owner_username": "trevor",
        "type": "daily",
        "title_key": "quests.daily.flashcards.title",
        "description_key": "quests.daily.flashcards.desc",
        "emoji": "🃏",
        "progress_current": 15,
        "progress_target": 15,
        "progress_unit": "cards",
        "reward_lingots": 3,
        "reward_xp": 5,
        "reward_ad_free_minutes": 0,
        "reward_streak_shield": False,
        "status": "claimable",
        "expires_in_hours": 16,
    },
    {
        "id": "quest-weekly-three-lessons",
        "owner_username": "trevor",
        "type": "weekly",
        "title_key": "quests.weekly.threeLessons.title",
        "description_key": "quests.weekly.threeLessons.desc",
        "emoji": "📚",
        "progress_current": 2,
        "progress_target": 5,
        "progress_unit": "lessons",
        "reward_lingots": 25,
        "reward_xp": 50,
        "reward_ad_free_minutes": 0,
        "reward_streak_shield": True,
        "status": "active",
        "expires_in_hours": 24 * 4,
    },
    {
        "id": "quest-weekly-master-row",
        "owner_username": "trevor",
        "type": "weekly",
        "title_key": "quests.weekly.masterRow.title",
        "description_key": "quests.weekly.masterRow.desc",
        "emoji": "★",
        "progress_current": 0,
        "progress_target": 1,
        "progress_unit": "modules",
        "reward_lingots": 30,
        "reward_xp": 75,
        "reward_ad_free_minutes": 0,
        "reward_streak_shield": False,
        "status": "active",
        "expires_in_hours": 24 * 4,
    },
    {
        "id": "quest-random-try-story",
        "owner_username": "trevor",
        "type": "random",
        "title_key": "quests.random.tryStory.title",
        "description_key": "quests.random.tryStory.desc",
        "emoji": "📖",
        "progress_current": 0,
        "progress_target": 1,
        "progress_unit": "stories",
        "reward_lingots": 8,
        "reward_xp": 0,
        "reward_ad_free_minutes": 15,
        "reward_streak_shield": False,
        "status": "active",
        "expires_in_hours": 6,
    },
    {
        "id": "quest-friend-overtake-sora",
        "owner_username": "trevor",
        "type": "friend",
        "title_key": "quests.friend.overtake.title",
        "description_key": "quests.friend.overtake.desc",
        "emoji": "🤝",
        "progress_current": 40,
        "progress_target": 100,
        "progress_unit": "XP",
        "reward_lingots": 20,
        "reward_xp": 40,
        "reward_ad_free_minutes": 0,
        "reward_streak_shield": False,
        "status": "active",
        "friend_username": "sora_n5",
        "friend_display_name": "Sora",
        "expires_in_hours": 24 * 4,
    },
]

QUESTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS quests (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,
    type                     TEXT NOT NULL,
    title_key                TEXT NOT NULL,
    description_key          TEXT NOT NULL,
    emoji                    TEXT NOT NULL DEFAULT '',
    progress_current         INTEGER NOT NULL DEFAULT 0,
    progress_target          INTEGER NOT NULL,
    progress_unit            TEXT NOT NULL DEFAULT '',
    reward_lingots           INTEGER NOT NULL DEFAULT 0,
    reward_xp                INTEGER NOT NULL DEFAULT 0,
    reward_ad_free_minutes   INTEGER NOT NULL DEFAULT 0,
    reward_streak_shield     INTEGER NOT NULL DEFAULT 0,
    status                   TEXT NOT NULL DEFAULT 'active',
    friend_id                TEXT,
    friend_display_name      TEXT,
    expires_at               TEXT,
    reward_granted           INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quests_user ON quests (user_id, created_at DESC);
"""

# (thread_id, from_username, body, hours_ago)
SEED_MESSAGES: list[tuple[str, str, str, float]] = [
    ("thread-sora", "trevor", "yo nice streak!", 20.0),
    ("thread-sora", "sora_n5", "thanks 🔥 hbu", 19.5),
    ("thread-sora", "trevor", "8 days in. trying to push past 10", 19.0),
    ("thread-sora", "sora_n5", "you got this. the M3 review helped me", 4.0),
    ("thread-sora", "sora_n5", "ありがとう btw for the kanji tip", 0.3),
    ("thread-kenji", "trevor", "studying tonight?", 30.0),
    ("thread-kenji", "kenji_dev", "yeah, leaderboard reset Fri 👀", 29.5),
    ("thread-kenji", "trevor", "20 days into my streak. you?", 29.0),
    ("thread-kenji", "kenji_dev", "47 lol. catch up", 28.0),
]


def _rand_invite_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


# Deck seed data: manifest + cards. Matches docs/dataformats/flashcards/ examples.
SEED_DECKS = [
    {
        "id": "ko-beginner",
        # Attribution: who shows up as the deck's maintainer on community
        # browse. Resolved to internal user_id when inserted.
        "maintainerAuth0Id": "dev|user-3",
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
        "maintainerAuth0Id": "dev|noor_x",
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
        "maintainerAuth0Id": "dev|kenji_dev",
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
        "maintainerAuth0Id": "dev|aiko_kanji",
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
        "maintainerAuth0Id": "dev|sora_n5",
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

-- Social: friend graph + blocks (split tables, matching app/db/sqlite/social.py).
CREATE TABLE IF NOT EXISTS social_friends (
    user_id     TEXT NOT NULL,
    friend_id   TEXT NOT NULL,
    friended_at TEXT NOT NULL,
    PRIMARY KEY (user_id, friend_id)
);
CREATE INDEX IF NOT EXISTS idx_social_friends_user ON social_friends (user_id);

CREATE TABLE IF NOT EXISTS social_friend_requests (
    from_id      TEXT NOT NULL,
    to_id        TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id)
);
CREATE INDEX IF NOT EXISTS idx_social_friend_requests_to ON social_friend_requests (to_id);
CREATE INDEX IF NOT EXISTS idx_social_friend_requests_from ON social_friend_requests (from_id);

CREATE TABLE IF NOT EXISTS social_blocks (
    blocker_id TEXT NOT NULL,
    blocked_id TEXT NOT NULL,
    blocked_at TEXT NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id)
);

CREATE TABLE IF NOT EXISTS social_leaderboard (
    bucket       TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    xp           INTEGER NOT NULL DEFAULT 0,
    lessons      INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (bucket, user_id)
);
CREATE INDEX IF NOT EXISTS idx_leaderboard_bucket_xp
    ON social_leaderboard(bucket, xp DESC);
"""

# ── Social extension tables ─────────────────────────────────────────────────
# These match the schemas the parallel backend agent is preparing. They are
# IF-NOT-EXISTS so they're safe to run twice (once here, once when their
# repo's ``connect()`` runs). If the parallel agent picks slightly different
# column names, update the INSERTs below — the data layout (one row per
# activity, one row per reaction, etc.) is stable.

SOCIAL_EXTENSION_SQL = """
CREATE TABLE IF NOT EXISTS social_activity (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_activity_user_time
    ON social_activity(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_activity_time
    ON social_activity(created_at DESC);

CREATE TABLE IF NOT EXISTS social_activity_reactions (
    activity_id TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (activity_id, user_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_social_activity_reactions_activity
    ON social_activity_reactions (activity_id);

CREATE TABLE IF NOT EXISTS social_invite_codes (
    code        TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_invite_redemptions (
    code         TEXT NOT NULL,
    invitee_id   TEXT NOT NULL,
    inviter_id   TEXT NOT NULL,
    status       TEXT NOT NULL,
    redeemed_at  TEXT NOT NULL,
    year_month   TEXT NOT NULL,
    PRIMARY KEY (code, invitee_id)
);
CREATE INDEX IF NOT EXISTS idx_social_invite_redemptions_inviter_month
    ON social_invite_redemptions (inviter_id, year_month);

CREATE TABLE IF NOT EXISTS social_threads (
    id          TEXT PRIMARY KEY,
    user_a_id   TEXT NOT NULL,
    user_b_id   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_threads_user_a ON social_threads (user_a_id);
CREATE INDEX IF NOT EXISTS idx_social_threads_user_b ON social_threads (user_b_id);

CREATE TABLE IF NOT EXISTS social_messages (
    id         TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL,
    sender_id  TEXT NOT NULL,
    body       TEXT NOT NULL,
    sent_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_messages_thread_time
    ON social_messages (thread_id, sent_at);
"""


async def reset(db: aiosqlite.Connection) -> None:
    print("  Dropping tables...")
    # Social-context tables (added by parallel agent + seeded here).
    for tbl in (
        "quests",
        "social_messages",
        "social_threads",
        "social_invite_redemptions",
        "social_invite_codes",
        "social_activity_reactions",
        "social_activity",
        "social_leaderboard",
        "social_blocks",
        "social_friend_requests",
        "social_friends",
        "social",
        "subscriptions",
        "deck_tags",
        "tags",
        "deck_content",
        "deck_manifests",
        "user_settings",
        "users",
    ):
        await db.execute(f"DROP TABLE IF EXISTS {tbl}")
    await db.commit()


async def seed(db_path: str, do_reset: bool) -> None:
    print(f"Database: {db_path}")
    db = await aiosqlite.connect(db_path)

    if do_reset:
        await reset(db)
    else:
        # Drop subscriptions if it has old schema (auth0_id) so INIT_SQL can create it with user_id
        cur = await db.execute("PRAGMA table_info(subscriptions)")
        sub_cols = [r[1] for r in await cur.fetchall()]
        if sub_cols and "user_id" not in sub_cols:
            await db.execute("DROP TABLE IF EXISTS subscriptions")
            await db.commit()

    await db.executescript(INIT_SQL)
    await db.executescript(SOCIAL_EXTENSION_SQL)
    await db.executescript(QUESTS_TABLE_SQL)

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
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id)"
        )
        await db.commit()

    # Migration: user ban fields, bio, role, progress columns (see ADR-0001).
    for col, col_def in [
        ("status_expiration", "TEXT"),
        ("community_status", "TEXT"),
        ("community_status_expiration", "TEXT"),
        ("bio", "TEXT"),
        ("role", "TEXT NOT NULL DEFAULT 'user'"),
        ("xp", "INTEGER NOT NULL DEFAULT 0"),
        ("level", "INTEGER NOT NULL DEFAULT 1"),
        ("lingots", "INTEGER NOT NULL DEFAULT 0"),
        ("streak", "INTEGER NOT NULL DEFAULT 0"),
        ("best_streak", "INTEGER NOT NULL DEFAULT 0"),
        ("last_active_date", "TEXT"),
    ]:
        try:
            await db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
            await db.commit()
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

    # Migration: ensure trevor (dev user) has admin role
    cur = await db.execute("PRAGMA table_info(users)")
    cols = [r[1] for r in await cur.fetchall()]
    if "role" in cols:
        await db.execute("UPDATE users SET role = 'admin' WHERE username = 'trevor'")
        await db.commit()

    # Migration: copy legacy status (user bio) to bio, normalize status to active/banned
    cur = await db.execute("PRAGMA table_info(users)")
    cols = [r[1] for r in await cur.fetchall()]
    if "bio" in cols:
        await db.execute(
            """UPDATE users SET bio = status, status = 'active'
               WHERE status NOT IN ('active', 'banned') AND (bio IS NULL OR bio = '')"""
        )
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
                                  profile_picture_key, status, role, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                u["auth0_id"],
                u["username"],
                u["display_name"],
                u["profile_picture_key"],
                u["status"],
                u.get("role", "user"),
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
        existing = await cur.fetchone()
        if existing:
            # Backfill author_id on existing seeded decks if missing — keeps
            # the seed idempotent and lets `--reset`-less reruns wire newly
            # added maintainer attributions without dropping the DB.
            maintainer_auth0 = deck.get("maintainerAuth0Id")
            backfill_author_id = (
                auth0_to_user_id.get(maintainer_auth0) if maintainer_auth0 else None
            )
            if backfill_author_id:
                await db.execute(
                    """UPDATE deck_manifests
                          SET author_id = COALESCE(author_id, ?)
                        WHERE id = ?""",
                    (backfill_author_id, deck["id"]),
                )
            continue
        manifest = deck["manifest"]
        cards = deck["cards"]
        # Resolve maintainer auth0_id → internal user_id so the deck's author_id
        # points at a real seeded user. Falls back to the manifest's authorId or
        # NULL if the maintainer didn't seed (e.g. inactive/banned user removed
        # from SEED_USERS). The contributors browse + maintainer chip on
        # community cards both join through author_id → users.
        maintainer_auth0 = deck.get("maintainerAuth0Id")
        author_id = (
            auth0_to_user_id.get(maintainer_auth0)
            if maintainer_auth0
            else manifest.get("authorId")
        ) or manifest.get("authorId")
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
                author_id,
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

    # ── Per-user progress (XP / streak / lingots / level / last_active_date) ──
    username_to_user_id: dict[str, str] = {}
    for auth0_id, uid in auth0_to_user_id.items():
        cur = await db.execute("SELECT username FROM users WHERE id = ?", (uid,))
        row = await cur.fetchone()
        if row:
            username_to_user_id[row[0]] = uid

    progress_updated = 0
    for username, xp, streak, _weekly, lingots, days_ago in SEED_PROGRESS:
        uid = username_to_user_id.get(username)
        if not uid:
            continue
        last_active = (datetime.now(UTC) - timedelta(days=days_ago)).date().isoformat()
        await db.execute(
            """UPDATE users
                  SET xp = ?,
                      level = ?,
                      lingots = ?,
                      streak = ?,
                      best_streak = MAX(best_streak, ?),
                      last_active_date = ?
                WHERE id = ?""",
            (xp, _level_for_xp(xp), lingots, streak, streak, last_active, uid),
        )
        progress_updated += 1

    # ── Friend graph ────────────────────────────────────────────────────────
    # Insert FRIEND rows in both directions (mirrored, matches the live repo's
    # ``send_friend_request`` / ``accept_friend_request`` behavior).
    trevor_id = username_to_user_id.get("trevor")
    friendship_pairs: list[tuple[str, str]] = []
    if trevor_id:
        for friend_name in TREVOR_FRIENDS:
            fid = username_to_user_id.get(friend_name)
            if fid:
                friendship_pairs.append((trevor_id, fid))
    for a_name, b_name in OTHER_FRIENDSHIPS:
        a = username_to_user_id.get(a_name)
        b = username_to_user_id.get(b_name)
        if a and b:
            friendship_pairs.append((a, b))

    friendship_rows = 0
    for owner_id, other_id in friendship_pairs:
        # spread created_at over the past 30 days for realism
        days_back = random.Random(owner_id + other_id).randint(1, 30)
        ts = (datetime.now(UTC) - timedelta(days=days_back)).isoformat()
        for o, t in ((owner_id, other_id), (other_id, owner_id)):
            await db.execute(
                """INSERT OR IGNORE INTO social_friends
                       (user_id, friend_id, friended_at)
                   VALUES (?, ?, ?)""",
                (o, t, ts),
            )
            friendship_rows += 1

    # ── Friend requests ─────────────────────────────────────────────────────
    request_rows = 0
    if trevor_id:
        # Incoming TO Trevor (requester → trevor)
        for name in INCOMING_REQUESTS_TO_TREVOR:
            rid = username_to_user_id.get(name)
            if not rid:
                continue
            ts = (
                datetime.now(UTC) - timedelta(hours=random.Random(name).randint(1, 72))
            ).isoformat()
            await db.execute(
                """INSERT OR IGNORE INTO social_friend_requests
                       (from_id, to_id, requested_at)
                   VALUES (?, ?, ?)""",
                (rid, trevor_id, ts),
            )
            request_rows += 1

        # Outgoing FROM Trevor (trevor → target)
        for name in OUTGOING_REQUESTS_FROM_TREVOR:
            tid = username_to_user_id.get(name)
            if not tid:
                continue
            ts = (
                datetime.now(UTC) - timedelta(hours=random.Random(name).randint(1, 72))
            ).isoformat()
            await db.execute(
                """INSERT OR IGNORE INTO social_friend_requests
                       (from_id, to_id, requested_at)
                   VALUES (?, ?, ?)""",
                (trevor_id, tid, ts),
            )
            request_rows += 1

    # ── Blocks ──────────────────────────────────────────────────────────────
    block_rows = 0
    if trevor_id:
        for name in TREVOR_BLOCKS:
            bid = username_to_user_id.get(name)
            if not bid:
                continue
            ts = datetime.now(UTC).isoformat()
            await db.execute(
                """INSERT OR IGNORE INTO social_blocks
                       (blocker_id, blocked_id, blocked_at)
                   VALUES (?, ?, ?)""",
                (trevor_id, bid, ts),
            )
            block_rows += 1

    # ── Leaderboards ────────────────────────────────────────────────────────
    # Write each progress entry into both the weekly and monthly buckets for
    # their learning language. This lets the leaderboard endpoints return
    # something interesting against a freshly-seeded DB.
    now_dt = datetime.now(UTC)
    iso = now_dt.isocalendar()
    leaderboard_rows = 0
    for username, _xp, _streak, weekly_xp, _lingots, _days in SEED_PROGRESS:
        if weekly_xp <= 0:
            continue
        uid = username_to_user_id.get(username)
        if not uid:
            continue
        prefs = (
            SEED_SETTINGS.get(f"dev|{username}")
            if username not in ("trevor", "hana", "testuser")
            else SEED_SETTINGS.get(
                {"trevor": DEV_USER, "hana": "dev|user-2", "testuser": "dev|user-3"}[username]
            )
        )
        lang = (prefs or {}).get("learningLanguage") or "ja"
        week_bucket = f"{lang}#{iso.year:04d}-W{iso.week:02d}"
        month_bucket = f"{lang}#{now_dt.year:04d}-{now_dt.month:02d}"
        # weekly lessons heuristic — divide weekly_xp by ~50 XP/lesson
        weekly_lessons = max(1, weekly_xp // 50)
        ts = now_dt.isoformat()
        for bucket in (week_bucket, month_bucket):
            xp_for_bucket = weekly_xp if bucket == week_bucket else weekly_xp * 4
            lessons_for_bucket = weekly_lessons if bucket == week_bucket else weekly_lessons * 4
            await db.execute(
                """INSERT INTO social_leaderboard
                       (bucket, user_id, xp, lessons, last_updated)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(bucket, user_id) DO UPDATE SET
                       xp = excluded.xp,
                       lessons = excluded.lessons,
                       last_updated = excluded.last_updated""",
                (bucket, uid, int(xp_for_bucket), int(lessons_for_bucket), ts),
            )
            leaderboard_rows += 1

    # ── Activity feed ───────────────────────────────────────────────────────
    activity_rows = 0
    activity_id_set: set[str] = set()
    for a in SEED_ACTIVITY:
        actor_id = username_to_user_id.get(a["actor"])
        if not actor_id:
            continue
        created_at = (now_dt - timedelta(days=a["days_ago"], hours=a["hours_ago"])).isoformat()
        payload_json = json.dumps({"text": a["text"]})
        await db.execute(
            """INSERT OR IGNORE INTO social_activity
                   (id, user_id, kind, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (a["id"], actor_id, a["kind"], payload_json, created_at),
        )
        activity_id_set.add(a["id"])
        activity_rows += 1

    # ── Reactions ───────────────────────────────────────────────────────────
    reaction_rows = 0
    for activity_id, reacter_name, kind in SEED_REACTIONS:
        if activity_id not in activity_id_set:
            continue
        reacter_id = username_to_user_id.get(reacter_name)
        if not reacter_id:
            continue
        ts = (
            now_dt - timedelta(minutes=random.Random(activity_id + reacter_name).randint(1, 600))
        ).isoformat()
        await db.execute(
            """INSERT OR IGNORE INTO social_activity_reactions
                   (activity_id, user_id, kind, created_at)
               VALUES (?, ?, ?, ?)""",
            (activity_id, reacter_id, kind, ts),
        )
        reaction_rows += 1

    # ── Invite code + redemptions ───────────────────────────────────────────
    invite_codes_rows = 0
    redemption_rows = 0
    if trevor_id:
        await db.execute(
            """INSERT OR IGNORE INTO social_invite_codes
                   (code, owner_id, created_at)
               VALUES (?, ?, ?)""",
            (
                TREVOR_INVITE_CODE,
                trevor_id,
                (now_dt - timedelta(days=30)).isoformat(),
            ),
        )
        invite_codes_rows += 1

        for r in SEED_INVITE_REDEMPTIONS:
            invitee_id = username_to_user_id.get(r["invitee_username"])
            if not invitee_id:
                continue
            created_at = (now_dt - timedelta(days=r["days_ago"])).isoformat()
            await db.execute(
                """INSERT OR IGNORE INTO social_invite_redemptions
                       (code, invitee_id, inviter_id, status, redeemed_at, year_month)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    r["code"],
                    invitee_id,
                    trevor_id,
                    r["status"],
                    created_at,
                    created_at[:7],
                ),
            )
            redemption_rows += 1

    # ── Threads + messages ──────────────────────────────────────────────────
    thread_rows = 0
    message_rows = 0
    # Pre-compute messages per thread to derive last_message + last_at.
    thread_msg_buckets: dict[str, list[tuple[str, str, str, float]]] = {}
    for t in SEED_THREADS:
        thread_msg_buckets[t["id"]] = []
    for msg in SEED_MESSAGES:
        thread_msg_buckets.setdefault(msg[0], []).append(msg)

    for t in SEED_THREADS:
        ua = username_to_user_id.get(t["user_a"])
        ub = username_to_user_id.get(t["user_b"])
        if not ua or not ub:
            continue
        msgs = thread_msg_buckets.get(t["id"], [])
        if msgs:
            most_recent = min(msgs, key=lambda m: m[3])
            updated_at = (now_dt - timedelta(hours=most_recent[3])).isoformat()
        else:
            updated_at = (now_dt - timedelta(days=2)).isoformat()
        await db.execute(
            """INSERT OR IGNORE INTO social_threads
                   (id, user_a_id, user_b_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                t["id"],
                ua,
                ub,
                (now_dt - timedelta(days=2)).isoformat(),
                updated_at,
            ),
        )
        thread_rows += 1

        for idx, (thread_id, from_name, body, hours_ago) in enumerate(thread_msg_buckets[t["id"]]):
            from_id = username_to_user_id.get(from_name)
            if not from_id:
                continue
            msg_id = f"{thread_id}-msg-{idx:02d}"
            ts = (now_dt - timedelta(hours=hours_ago)).isoformat()
            await db.execute(
                """INSERT OR IGNORE INTO social_messages
                       (id, thread_id, sender_id, body, sent_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (msg_id, thread_id, from_id, body, ts),
            )
            message_rows += 1

    # ── Quests ──────────────────────────────────────────────────────────────
    quest_rows = 0
    for q in SEED_QUESTS:
        owner_id = username_to_user_id.get(q["owner_username"])
        if not owner_id:
            continue
        friend_username = q.get("friend_username")
        friend_id = username_to_user_id.get(friend_username) if friend_username else None
        expires_at = (
            now_dt + timedelta(hours=q.get("expires_in_hours", 24))
        ).isoformat()
        await db.execute(
            """INSERT OR IGNORE INTO quests (
                id, user_id, type, title_key, description_key, emoji,
                progress_current, progress_target, progress_unit,
                reward_lingots, reward_xp, reward_ad_free_minutes,
                reward_streak_shield, status, friend_id, friend_display_name,
                expires_at, reward_granted, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                q["id"],
                owner_id,
                q["type"],
                q["title_key"],
                q["description_key"],
                q.get("emoji") or "",
                int(q.get("progress_current") or 0),
                int(q["progress_target"]),
                q.get("progress_unit") or "",
                int(q.get("reward_lingots") or 0),
                int(q.get("reward_xp") or 0),
                int(q.get("reward_ad_free_minutes") or 0),
                1 if q.get("reward_streak_shield") else 0,
                q.get("status") or "active",
                friend_id,
                q.get("friend_display_name"),
                expires_at,
                0,
                now_dt.isoformat(),
            ),
        )
        quest_rows += 1

    await db.commit()
    await db.close()

    print(f"  Users:        {created} created, {skipped} skipped (already exist)")
    print(f"  Settings:     {settings_created} created")
    print(f"  Decks:        {deck_created} created")
    print(f"  Progress:     {progress_updated} users updated")
    print(f"  Friendships:  {friendship_rows} rows (mirrored)")
    print(f"  Requests:     {request_rows} rows (mirrored)")
    print(f"  Blocks:       {block_rows}")
    print(f"  Leaderboard:  {leaderboard_rows} rows")
    print(f"  Activity:     {activity_rows} items")
    print(f"  Reactions:    {reaction_rows}")
    print(f"  Invite codes: {invite_codes_rows}")
    print(f"  Redemptions:  {redemption_rows}")
    print(f"  Threads:      {thread_rows}")
    print(f"  Messages:     {message_rows}")
    print(f"  Quests:       {quest_rows}")
    print("Done.")


# ── Tags ────────────────────────────────────────────────────────────────────
# Admin-curated, canonical tag dictionary. Slug pattern: ``^[a-z][a-z0-9-]{1,40}$``
# (lowercase, starts with a letter, dashes for word breaks). Display name is
# the human-readable label the FE shows on facet chips + the deck card meta
# row. The starter set covers JLPT levels, writing systems, and the broad
# topical buckets the community browse + create-deck pickers need on day one.

SEED_TAGS: list[dict] = [
    {"slug": "jlpt-n5", "display_name": "JLPT N5", "description": "Beginner Japanese", "color": "#3b82f6"},
    {"slug": "jlpt-n4", "display_name": "JLPT N4", "description": "Upper beginner Japanese", "color": "#6366f1"},
    {"slug": "hiragana", "display_name": "Hiragana", "description": "Japanese phonetic script", "color": "#f97316"},
    {"slug": "katakana", "display_name": "Katakana", "description": "Japanese phonetic script (loanwords)", "color": "#fb923c"},
    {"slug": "kanji", "display_name": "Kanji", "description": "Japanese logographic characters", "color": "#ef4444"},
    {"slug": "vocabulary", "display_name": "Vocabulary", "description": "Word lists and meanings", "color": "#22c55e"},
    {"slug": "grammar", "display_name": "Grammar", "description": "Sentence patterns and rules", "color": "#14b8a6"},
    {"slug": "kdrama", "display_name": "K-Drama", "description": "Korean drama phrases", "color": "#ec4899"},
    {"slug": "anime", "display_name": "Anime", "description": "Anime / manga language", "color": "#a855f7"},
    {"slug": "travel", "display_name": "Travel", "description": "Travel phrases and survival language", "color": "#0ea5e9"},
    {"slug": "business", "display_name": "Business", "description": "Workplace and formal language", "color": "#64748b"},
    {"slug": "food", "display_name": "Food", "description": "Food, drinks, restaurants", "color": "#f59e0b"},
    {"slug": "culture", "display_name": "Culture", "description": "Cultural notes and context", "color": "#84cc16"},
]


TAGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tags (
    slug         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description  TEXT,
    color        TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deck_tags (
    deck_id   TEXT NOT NULL,
    tag_slug  TEXT NOT NULL,
    PRIMARY KEY (deck_id, tag_slug)
);
CREATE INDEX IF NOT EXISTS idx_deck_tags_deck ON deck_tags (deck_id);
CREATE INDEX IF NOT EXISTS idx_deck_tags_tag  ON deck_tags (tag_slug);
"""


async def seed_tags(db_path: str) -> None:
    """Idempotent — INSERT OR IGNORE so re-running the seed is a no-op once
    the starter tags exist. Safe to call independent of the main seed."""
    db = await aiosqlite.connect(db_path)
    try:
        await db.executescript(TAGS_TABLE_SQL)
        now = datetime.now(UTC).isoformat()
        inserted = 0
        for tag in SEED_TAGS:
            cur = await db.execute("SELECT 1 FROM tags WHERE slug = ?", (tag["slug"],))
            if await cur.fetchone():
                continue
            await db.execute(
                """INSERT INTO tags (slug, display_name, description, color, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    tag["slug"],
                    tag["display_name"],
                    tag.get("description"),
                    tag.get("color"),
                    now,
                ),
            )
            inserted += 1
        await db.commit()
        print(f"  Tags:         {inserted} created, {len(SEED_TAGS) - inserted} skipped")
    finally:
        await db.close()


if __name__ == "__main__":
    do_reset = "--reset" in sys.argv
    asyncio.run(seed(settings.SQLITE_PATH, do_reset))
    asyncio.run(seed_tags(settings.SQLITE_PATH))
