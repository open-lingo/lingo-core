"""Sample in-app shop catalog — server is source of truth for prices."""

from typing import Any, TypedDict


class ShopItemDef(TypedDict):
    id: str
    price: int
    category: str
    consumable: bool


SHOP_ITEMS: dict[str, ShopItemDef] = {
    "streak-freeze": {
        "id": "streak-freeze",
        "price": 10,
        "category": "powerups",
        "consumable": True,
    },
    "hint-pack": {
        "id": "hint-pack",
        "price": 5,
        "category": "powerups",
        "consumable": True,
    },
    "profile-frame-gold": {
        "id": "profile-frame-gold",
        "price": 25,
        "category": "cosmetics",
        "consumable": False,
    },
    "profile-frame-silver": {
        "id": "profile-frame-silver",
        "price": 250,
        "category": "cosmetics",
        "consumable": False,
    },
    "profile-frame-bronze": {
        "id": "profile-frame-bronze",
        "price": 150,
        "category": "cosmetics",
        "consumable": False,
    },
    "profile-frame-blue": {
        "id": "profile-frame-blue",
        "price": 500,
        "category": "cosmetics",
        "consumable": False,
    },
    "profile-frame-emerald": {
        "id": "profile-frame-emerald",
        "price": 500,
        "category": "cosmetics",
        "consumable": False,
    },
    "profile-frame-rose": {
        "id": "profile-frame-rose",
        "price": 500,
        "category": "cosmetics",
        "consumable": False,
    },
    "profile-frame-plasma": {
        "id": "profile-frame-plasma",
        "price": 1000,
        "category": "cosmetics",
        "consumable": False,
    },
    "title-night-owl": {
        "id": "title-night-owl",
        "price": 15,
        "category": "cosmetics",
        "consumable": False,
    },
    # ─── Profile banners ────────────────────────────────────────────────
    # Inline-SVG art lives on the frontend; the backend just gates purchase
    # + records ownership. IDs and prices MUST match
    # ``lingo/src/features/shop/shopCatalog.ts``.
    "banner-sakura": {
        "id": "banner-sakura",
        "price": 200,
        "category": "cosmetics",
        "consumable": False,
    },
    "banner-vaporwave-sun": {
        "id": "banner-vaporwave-sun",
        "price": 350,
        "category": "cosmetics",
        "consumable": False,
    },
    "banner-starry-night": {
        "id": "banner-starry-night",
        "price": 250,
        "category": "cosmetics",
        "consumable": False,
    },
    "banner-hearts-confetti": {
        "id": "banner-hearts-confetti",
        "price": 150,
        "category": "cosmetics",
        "consumable": False,
    },
    "banner-coffee-steam": {
        "id": "banner-coffee-steam",
        "price": 150,
        "category": "cosmetics",
        "consumable": False,
    },
    "banner-sunset-mountains": {
        "id": "banner-sunset-mountains",
        "price": 300,
        "category": "cosmetics",
        "consumable": False,
    },
    "banner-ocean-boat": {
        "id": "banner-ocean-boat",
        "price": 300,
        "category": "cosmetics",
        "consumable": False,
    },
    "banner-meadow": {
        "id": "banner-meadow",
        "price": 200,
        "category": "cosmetics",
        "consumable": False,
    },
}


def get_shop_item(item_id: str) -> ShopItemDef | None:
    return SHOP_ITEMS.get(item_id)


def list_shop_items() -> list[dict[str, Any]]:
    return list(SHOP_ITEMS.values())
