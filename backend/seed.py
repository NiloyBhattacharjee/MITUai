"""Add business.yaml menu items that aren't in the database yet, plus a few sample orders and bookings.
Items already in the database are left alone: after the first load they're managed on the dashboard's Menu page.
Usage: python seed.py            (add new menu items, add samples if there are no orders yet)
       python seed.py --if-empty (do nothing if the menu already exists; used by docker-compose)
       python seed.py --demo     (also add ~60 days of random orders and bookings, to see the analytics page filled in)
"""
import random
import sys
from datetime import datetime, time, timedelta

from sqlalchemy import func, select

from app import hours
from app.config import BUSINESS, cents
from app.db import Booking, MenuItem, Order, Session, init_db, new_item


def order(items: list[MenuItem], placed: datetime, status: str, way: str, name: str) -> Order:
    """placed: local time."""
    lines = []
    for i in items:
        picked = [g["choices"][0]["name"] for g in i.options if g["required"]]
        lines.append({"item_id": i.id, "name": i.name, "quantity": random.choice([1, 1, 2]), "options": picked, "notes": "",
                      "unit_price": i.price})
    subtotal = sum(line["unit_price"] * line["quantity"] for line in lines)
    fee = cents(BUSINESS["fulfilment"][way].get("fee", 0))
    return Order(customer_name=name, phone=f"04{random.randint(10000000, 99999999)}", fulfilment=way,
                 address="12 Example St, Newtown" if way == "delivery" else None, items=lines, subtotal=subtotal, fee=fee,
                 total=subtotal + fee, payment_method=next(iter(BUSINESS["payment_methods"])), status=status,
                 ready_at=hours.to_utc(placed + timedelta(minutes=20)), created_at=hours.to_utc(placed))


if __name__ == "__main__":
    init_db()
    with Session() as db:
        if "--if-empty" in sys.argv and db.scalar(select(func.count(MenuItem.id))):
            sys.exit(0)
        for data in BUSINESS["menu"]:
            if not db.get(MenuItem, data["id"]):
                db.add(new_item(data))
        db.commit()
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

        if "--demo" in sys.argv:
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
    print("Seeded.")
