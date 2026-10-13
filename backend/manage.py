"""Run the cafés on this install.

    python manage.py list
    python manage.py add-shop ID business.yaml --owner-password PASSWORD
    python manage.py update-shop ID business.yaml       (its settings from the file, plus menu items it doesn't have yet)
    python manage.py add-staff ID NAME PASSWORD
    python manage.py connect ID [--whatsapp-number-id X] [--whatsapp-token T] [--page-id P] [--instagram-id I] [--page-token T]

ID is the short name staff type to sign in, e.g. little-fern. connect stores the ids Meta sends with each webhook, so
messages reach the right café, and the tokens to reply with.
"""
import argparse
import re
import sys

from sqlalchemy import select

from app import auth, shops, tenancy
from app.db import Session, Shop, Staff, init_db


def get_shop(db, shop_id: str) -> Shop:
    return db.get(Shop, shop_id) or sys.exit(f"No business '{shop_id}'. See: python manage.py list")


def check_password(password: str):
    if problem := auth.strong_enough(password):
        sys.exit(problem)


def main(argv: list[str]):
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cmd = cli.add_subparsers(dest="cmd", required=True)
    cmd.add_parser("list")
    add = cmd.add_parser("add-shop")
    add.add_argument("id")
    add.add_argument("file")
    add.add_argument("--owner-password", required=True)
    upd = cmd.add_parser("update-shop")
    upd.add_argument("id")
    upd.add_argument("file")
    staff = cmd.add_parser("add-staff")
    staff.add_argument("id")
    staff.add_argument("name")
    staff.add_argument("password")
    con = cmd.add_parser("connect")
    con.add_argument("id")
    for flag in ("--whatsapp-number-id", "--whatsapp-token", "--page-id", "--instagram-id", "--page-token"):
        con.add_argument(flag)
    a = cli.parse_args(argv)

    init_db()
    with Session() as db:
        if a.cmd == "list":
            for s in db.scalars(select(Shop).order_by(Shop.id)):
                people = ", ".join(db.scalars(select(Staff.name).where(Staff.shop_id == s.id)))
                print(f"{s.id:20} {s.config.get('name', ''):28} WhatsApp {s.whatsapp_phone_number_id or '-':16} "
                      f"Page {s.page_id or '-':16} Instagram {s.instagram_id or '-':16} Staff: {people}")
        elif a.cmd == "add-shop":
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,39}", a.id):
                sys.exit("The ID is 2-40 lowercase letters, digits and dashes, e.g. little-fern")
            if db.get(Shop, a.id):
                sys.exit(f"'{a.id}' already exists")
            check_password(a.owner_password)
            shops.create(db, a.id, shops.read(a.file), {"Owner": a.owner_password})
            print(f"Created '{a.id}'. The owner signs in with business ID {a.id}. Next: python manage.py connect {a.id} ...")
        elif a.cmd == "update-shop":
            shop, data = get_shop(db, a.id), shops.read(a.file)
            shop.config = {k: v for k, v in data.items() if k != "menu"}
            db.commit()
            with tenancy.scope(shop):
                added = shops.add_new_menu_items(db, data.get("menu", []))
            print(f"Updated '{a.id}'s settings; {added} new menu items. Existing items are edited on the Menu page.")
        elif a.cmd == "add-staff":
            get_shop(db, a.id)
            check_password(a.password)
            if db.scalar(select(Staff).where(Staff.shop_id == a.id, Staff.name == a.name)):
                sys.exit(f"{a.name} already works at '{a.id}'")
            db.add(Staff(shop_id=a.id, name=a.name, password_hash=auth.hash_password(a.password)))
            db.commit()
            print(f"{a.name} can sign in to '{a.id}' now.")
        elif a.cmd == "connect":
            shop = get_shop(db, a.id)
            for flag, column in [("whatsapp_number_id", "whatsapp_phone_number_id"), ("whatsapp_token", "whatsapp_token"),
                                 ("page_id", "page_id"), ("instagram_id", "instagram_id"), ("page_token", "page_token")]:
                if getattr(a, flag) is not None:
                    setattr(shop, column, getattr(a, flag) or None)
            db.commit()
            print(f"Saved '{a.id}'s connections.")


if __name__ == "__main__":
    main(sys.argv[1:])
