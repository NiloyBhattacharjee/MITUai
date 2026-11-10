"""Create the first business from .env and business.yaml if the database has none, then add sample orders, bookings and appointments.
Usage: python seed.py              (the first business if missing, new menu items from business.yaml, samples if it has no orders)
       python seed.py --if-empty   (only create the first business, with samples, if there are no businesses; used by docker-compose)
       python seed.py --demo       (also ~60 days of random orders, bookings and appointments, to see the Overview filled in)
       python seed.py --shop ID    (samples for another business; add them with manage.py)
"""
import random
import sys
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from app import auth, config, hours, shops, tenancy, tools
from app.config import cents
from app.db import Booking, MenuItem, Order, Session, Shop, init_db


# Sample customers who fit where the business is: names, mobile prefix, address. Australian unless listed.
PEOPLE = {"Asia/Dhaka": (["Nusrat Jahan", "Tanvir Hasan", "Farhana Akter", "Rafiq Islam", "Sadia Rahman", "Arif Hossain", "Mim Chowdhury"],
                         "017", "Road 7, Banani")}
AUSTRALIA = (["Olivia Chen", "Jack Wilson", "Priya Sharma", "Liam Nguyen", "Sophie Brown", "Noah Martin", "Mia Rossi"], "04", "12 Example St, Newtown")


def people():
    return PEOPLE.get(hours.tz().key, AUSTRALIA)


def phone() -> str:
    return f"{people()[1]}{random.randint(10000000, 99999999)}"


def order(items: list[MenuItem], placed: datetime, status: str, way: str, name: str) -> Order:
    """placed: local time."""
    lines = []
    for i in items:
        picked = [g["choices"][0]["name"] for g in i.options if g["required"]]
        lines.append({"item_id": i.id, "name": i.name, "quantity": random.choice([1, 1, 2]), "options": picked, "notes": "",
                      "unit_price": i.price})
    subtotal = sum(line["unit_price"] * line["quantity"] for line in lines)
    fee = cents(tools.fulfilment()[way].get("fee", 0))
    return Order(customer_name=name, phone=phone(), fulfilment=way,
                 address=people()[2] if tools.fulfilment()[way].get("needs_address") else None, items=lines,
                 subtotal=subtotal, fee=fee, total=subtotal + fee, payment_method=random.choice(list(tools.payments())), status=status,
                 ready_at=hours.to_utc(placed + tools.prep()), created_at=hours.to_utc(placed))


def first_shop(db) -> Shop:
    owner = {"Owner": config.ADMIN_PASSWORD, **config.STAFF}
    for name, password in owner.items():
        if problem := auth.strong_enough(password):
            sys.exit(f"Password for {name} ({'ADMIN_PASSWORD' if name == 'Owner' else 'STAFF'} in .env): {problem}")
    shop = shops.create(db, config.FIRST_SHOP_ID, shops.read(config.BUSINESS_FILE), owner,
                        whatsapp_phone_number_id=config.WHATSAPP_PHONE_NUMBER_ID or None, whatsapp_token=config.WHATSAPP_TOKEN or None,
                        page_id=config.PAGE_ID or None, instagram_id=config.INSTAGRAM_ID or None, page_token=config.PAGE_ACCESS_TOKEN or None)
    print(f"Created '{shop.id}' from {config.BUSINESS_FILE.name}. Staff sign in with business ID {shop.id} and their password.")
    return shop


def add_new_items(db, shop: Shop, path) -> int | None:
    """New menu items from the business file, only if it is this business's file: switching BUSINESS_FILE to another
    kind of business must not put its products into the existing one."""
    business = shops.read(path)
    if business.get("name") != shop.config.get("name"):
        print(f"{path.name} is {business.get('name')}, not {shop.config.get('name')}: left the menu alone.")
        return None
    return shops.add_new_menu_items(db, business.get("menu", []))


def placed_on(day: date) -> datetime | None:
    """A random time the business is open that day (local), or None when it's closed."""
    if not (open_ := hours.periods(day)):
        return None
    start, end = random.choice(open_)
    return start + timedelta(minutes=random.randrange(int((end - start).total_seconds()) // 60))


def appointment(db, day: date, name: str) -> Booking | None:
    """A booked service that day with someone who does it and is free then; None if the day is full or closed."""
    service = random.choice(db.scalars(select(MenuItem).where(MenuItem.minutes.is_not(None), MenuItem.available)).all())
    step = tools.appts().get("slot_minutes", 15)
    starts = [s + timedelta(minutes=m) for s, e in hours.periods(day)
              for m in range(0, int((e - s).total_seconds()) // 60 - service.minutes + 1, step)]
    for start in random.sample(starts, len(starts)):
        if free := [p for p in tools.providers(service.id) if not tools._busy(db, p, start, start + timedelta(minutes=service.minutes))]:
            b = Booking(customer_name=name, phone=phone(), party_size=1, service_id=service.id, service=service.name,
                        provider=random.choice(free), minutes=service.minutes, at=hours.to_utc(start))
            db.add(b)
            db.flush()  # so the next _busy sees it
            return b
    return None


def samples(db, demo: bool):
    """Orders, table bookings and appointments, each only for a business that takes them."""
    menu = [i for i in db.scalars(select(MenuItem).where(MenuItem.minutes.is_(None))) if tools.in_stock(i)]
    ways = list(tools.fulfilment())
    names = people()[0]
    now = hours.local_now()
    if not db.scalar(select(func.count(Order.id))) and not db.scalar(select(func.count(Booking.id))):
        if ways:
            db.add_all([order(random.sample(menu, 2), now - timedelta(minutes=12), "new", ways[0], names[0]),
                        order(random.sample(menu, 1), now - timedelta(minutes=25), "preparing", ways[-1], names[1]),
                        order(random.sample(menu, 3), now - timedelta(days=1), "completed", ways[0], names[2])])
        if tools.tables():
            db.add(Booking(customer_name=names[3], phone=phone(), party_size=4, notes="High chair please",
                           at=hours.to_utc(datetime.combine(now.date() + timedelta(days=1), time(12, 30)))))
        for days, name in [(1, names[4]), (1, names[5]), (2, names[6])] if tools.appts() else ():
            appointment(db, now.date() + timedelta(days=days), name)
        db.commit()
    if demo:
        made = []
        for days_ago in range(60):
            day = now.date() - timedelta(days=days_ago)
            for _ in range(random.randint(2, 9) + (days_ago < 30) * 2) if ways else ():
                if placed := placed_on(day):
                    status = "cancelled" if random.random() < 0.05 else "completed" if days_ago else random.choice(["new", "preparing", "ready"])
                    db.add(order(random.sample(menu, random.choice([1, 2, 2, 3])), placed, status, random.choice(ways), random.choice(names)))
            for _ in range(random.randint(0, 4)) if tools.tables() else ():
                at = datetime.combine(day + timedelta(days=random.randint(1, 7)), time(random.choice([9, 10, 12, 13])))
                db.add(Booking(customer_name=random.choice(names), phone=phone(), party_size=random.randint(2, 6),
                               at=hours.to_utc(at), created_at=hours.to_utc(datetime.combine(day, time(10))),
                               status="cancelled" if random.random() < 0.08 else "booked"))
            for _ in range(random.randint(3, 8)) if tools.appts() else ():
                if b := appointment(db, day + timedelta(days=random.randint(0, 7)), random.choice(names)):
                    b.created_at = hours.to_utc(datetime.combine(day, time(10)))
                    made.append(b)
        # Past ones get their outcome only now: _busy counts booked appointments, so all of them had to be booked while placing.
        for b in made:
            if hours.to_local(b.at) < now:
                b.status = random.choices(["arrived", "no_show", "cancelled"], [90, 4, 6])[0]
            elif random.random() < 0.05:
                b.status = "cancelled"
        db.commit()


if __name__ == "__main__":
    init_db()
    shop_id = sys.argv[sys.argv.index("--shop") + 1] if "--shop" in sys.argv else config.FIRST_SHOP_ID
    with Session() as db:
        if not db.scalar(select(func.count(Shop.id))):
            first_shop(db)
        elif "--if-empty" in sys.argv:
            sys.exit(0)
        shop = db.get(Shop, shop_id) or sys.exit(f"No business '{shop_id}'. See: python manage.py list")
    with tenancy.scope(shop), Session() as db:
        if shop.id == config.FIRST_SHOP_ID:
            add_new_items(db, shop, config.BUSINESS_FILE)
        samples(db, "--demo" in sys.argv)
    print("Seeded.")
