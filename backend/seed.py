"""Create the first café from .env and business.yaml if the database has none, then add sample orders and a booking.
Usage: python seed.py              (the first café if missing, new menu items from business.yaml, samples if it has no orders)
       python seed.py --if-empty   (only create the first café, with samples, if there are no cafés; used by docker-compose)
       python seed.py --demo       (also ~60 days of random orders and bookings, to see the Overview filled in)
       python seed.py --shop ID    (samples for another café; add cafés with manage.py)
"""
import random
import sys
from datetime import datetime, time, timedelta

from sqlalchemy import func, select

from app import auth, config, hours, shops, tenancy
from app.config import cents
from app.db import Booking, MenuItem, Order, Session, Shop, init_db


def order(items: list[MenuItem], placed: datetime, status: str, way: str, name: str) -> Order:
    """placed: local time."""
    lines = []
    for i in items:
        picked = [g["choices"][0]["name"] for g in i.options if g["required"]]
        lines.append({"item_id": i.id, "name": i.name, "quantity": random.choice([1, 1, 2]), "options": picked, "notes": "",
                      "unit_price": i.price})
    subtotal = sum(line["unit_price"] * line["quantity"] for line in lines)
    fee = cents(tenancy.cfg()["fulfilment"][way].get("fee", 0))
    return Order(customer_name=name, phone=f"04{random.randint(10000000, 99999999)}", fulfilment=way,
                 address="12 Example St, Newtown" if way == "delivery" else None, items=lines, subtotal=subtotal, fee=fee,
                 total=subtotal + fee, payment_method=next(iter(tenancy.cfg()["payment_methods"])), status=status,
                 ready_at=hours.to_utc(placed + timedelta(minutes=20)), created_at=hours.to_utc(placed))


def first_shop(db) -> Shop:
    owner = {"Owner": config.ADMIN_PASSWORD, **config.STAFF}
    for name, password in owner.items():
        if problem := auth.strong_enough(password):
            sys.exit(f"Password for {name} ({'ADMIN_PASSWORD' if name == 'Owner' else 'STAFF'} in .env): {problem}")
    shop = shops.create(db, config.FIRST_SHOP_ID, shops.read(config.BUSINESS_FILE), owner,
                        whatsapp_phone_number_id=config.WHATSAPP_PHONE_NUMBER_ID or None, whatsapp_token=config.WHATSAPP_TOKEN or None,
                        page_id=config.PAGE_ID or None, instagram_id=config.INSTAGRAM_ID or None, page_token=config.PAGE_ACCESS_TOKEN or None)
    print(f"Created café '{shop.id}' from {config.BUSINESS_FILE.name}. Staff sign in with café ID {shop.id} and their password.")
    return shop


def samples(db, demo: bool):
    menu = db.scalars(select(MenuItem).where(MenuItem.available)).all()
    names = ["Olivia Chen", "Jack Wilson", "Priya Sharma", "Liam Nguyen", "Sophie Brown", "Noah Martin", "Mia Rossi"]
    if not db.scalar(select(func.count(Order.id))):
        now = hours.local_now()
        db.add_all([order(random.sample(menu, 2), now - timedelta(minutes=12), "new", "pickup", "Olivia Chen"),
                    order(random.sample(menu, 1), now - timedelta(minutes=25), "preparing", "delivery", "Jack Wilson"),
                    order(random.sample(menu, 3), now - timedelta(days=1), "completed", "pickup", "Priya Sharma")])
        tomorrow = now.date() + timedelta(days=1)
        db.add(Booking(customer_name="Liam Nguyen", phone="0412345678", party_size=4, notes="High chair please",
                       at=hours.to_utc(datetime.combine(tomorrow, time(12, 30)))))
        db.commit()
    if demo:
        today = hours.local_now().date()
        for days_ago in range(60):
            day = today - timedelta(days=days_ago)
            for _ in range(random.randint(2, 9) + (days_ago < 30) * 2):
                placed = datetime.combine(day, time(random.randint(7, 14), random.randint(0, 59)))
                status = "cancelled" if random.random() < 0.05 else "completed" if days_ago else random.choice(["new", "preparing", "ready"])
                db.add(order(random.sample(menu, random.choice([1, 2, 2, 3])), placed, status,
                             random.choice(["pickup", "pickup", "delivery"]), random.choice(names)))
            for _ in range(random.randint(0, 4)):
                at = datetime.combine(day + timedelta(days=random.randint(1, 7)), time(random.choice([9, 10, 12, 13])))
                db.add(Booking(customer_name=random.choice(names), phone="0400000000", party_size=random.randint(2, 6),
                               at=hours.to_utc(at), created_at=hours.to_utc(datetime.combine(day, time(10))),
                               status="cancelled" if random.random() < 0.08 else "booked"))
        db.commit()


if __name__ == "__main__":
    init_db()
    shop_id = sys.argv[sys.argv.index("--shop") + 1] if "--shop" in sys.argv else config.FIRST_SHOP_ID
    with Session() as db:
        if not db.scalar(select(func.count(Shop.id))):
            first_shop(db)
        elif "--if-empty" in sys.argv:
            sys.exit(0)
        shop = db.get(Shop, shop_id) or sys.exit(f"No café '{shop_id}'. See: python manage.py list")
    with tenancy.scope(shop), Session() as db:
        if shop.id == config.FIRST_SHOP_ID:
            shops.add_new_menu_items(db, shops.read(config.BUSINESS_FILE).get("menu", []))
        samples(db, "--demo" in sys.argv)
    print("Seeded.")
