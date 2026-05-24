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
    "title-night-owl": {
        "id": "title-night-owl",
        "price": 15,
        "category": "cosmetics",
        "consumable": False,
    },
}


def get_shop_item(item_id: str) -> ShopItemDef | None:
    return SHOP_ITEMS.get(item_id)


def list_shop_items() -> list[dict[str, Any]]:
    return list(SHOP_ITEMS.values())
