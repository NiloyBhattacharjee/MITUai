"""Creating and updating cafés from business.yaml files (used by manage.py, seed.py, the tests and the eval)."""
from pathlib import Path

import yaml
from sqlalchemy import select

from . import auth, tenancy
from .db import MenuItem, Session, Shop, Staff, new_item


def read(path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def create(db, shop_id: str, business: dict, staff: dict[str, str], **channels) -> Shop:
    """A café with its settings, its menu, and staff {name: password}. channels: whatsapp_phone_number_id, page_id, ..."""
    shop = Shop(id=shop_id, config={k: v for k, v in business.items() if k != "menu"}, **channels)
    db.add(shop)
    db.add_all(Staff(shop_id=shop_id, name=name, password_hash=auth.hash_password(pw)) for name, pw in staff.items())
    db.commit()
    with tenancy.scope(shop):
        add_new_menu_items(db, business.get("menu", []))
    return shop


def add_new_menu_items(db, menu: list[dict]) -> int:
    """Items from the file that the café doesn't have yet; existing ones are managed on the dashboard's Menu page."""
    have = set(db.scalars(select(MenuItem.id)))
    new = [new_item(i) for i in menu if i["id"] not in have]
    db.add_all(new)
    db.commit()
    return len(new)


def pick(shop_id: str | None = None) -> Shop:
    """For command-line tools: the named café, or the first one."""
    with Session() as db:
        shop = db.get(Shop, shop_id) if shop_id else db.scalar(select(Shop).order_by(Shop.created_at))
    if not shop:
        raise SystemExit(f"No business {shop_id or 'yet'}: run python seed.py (or manage.py add-shop) first.")
    return shop
