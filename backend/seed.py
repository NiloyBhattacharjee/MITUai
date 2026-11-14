"""Create the first business from .env and business.yaml if the database has none, then add sample orders.
Usage: python seed.py              (the first business if missing, new menu items from business.yaml, samples if it has no orders)
       python seed.py --if-empty   (only create the first business, with samples, if there are no businesses; used by docker-compose)
       python seed.py --demo       (also ~60 days of random orders, to see the Overview filled in)
       python seed.py --shop ID    (samples for another business; add them with manage.py)
"""
import random
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app import auth, config, hours, shops, tenancy, tools
from app.config import cents
from app.db import MenuItem, Order, Session, Shop, init_db


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
                        page_id=config.PAGE_ID or None, instagram_id=config.INSTAGRAM_ID or None, page_token=config.PAGE_ACCESS_TOKEN or None,
                        twilio_number=config.TWILIO_NUMBER or None)
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


def samples(db, demo: bool):
    """A few orders (if it has none yet), and with demo=True ~60 days of history, for a business that takes orders."""
    menu = [i for i in db.scalars(select(MenuItem)) if tools.in_stock(i)]
    ways = list(tools.fulfilment())
    names = people()[0]
    now = hours.local_now()
    if not ways:
        return
    if not db.scalar(select(func.count(Order.id))):
        db.add_all([order(random.sample(menu, 2), now - timedelta(minutes=12), "new", ways[0], names[0]),
                    order(random.sample(menu, 1), now - timedelta(minutes=25), "preparing", ways[-1], names[1]),
                    order(random.sample(menu, 3), now - timedelta(days=1), "completed", ways[0], names[2])])
    for days_ago in range(60) if demo else ():
        day = now.date() - timedelta(days=days_ago)
        for _ in range(random.randint(2, 9) + (days_ago < 30) * 2):
            if placed := placed_on(day):
                status = "cancelled" if random.random() < 0.05 else "completed" if days_ago else random.choice(["new", "preparing", "ready"])
                db.add(order(random.sample(menu, random.choice([1, 2, 2, 3])), placed, status, random.choice(ways), random.choice(names)))
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
