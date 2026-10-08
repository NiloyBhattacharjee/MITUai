"""Add business.yaml products that aren't in the database yet, plus sample orders.
Products already in the database are left alone: after the first load they're managed on the dashboard's Products page.
Usage: python seed.py            (add new products, add sample orders if there are none)
       python seed.py --if-empty (do nothing if products already exist; used by docker-compose)
       python seed.py --demo     (also add ~60 days of random demo orders, to see the analytics page filled in)
"""
import random
import sys
from datetime import timedelta

from sqlalchemy import func, select

from app.config import BUSINESS
from app.db import Order, Product, Session, init_db, new_product, now
from app.tools import quote_total

init_db()
with Session() as db:
    if "--if-empty" in sys.argv and db.scalar(select(func.count(Product.id))):
        sys.exit(0)
    for p in BUSINESS["products"]:
        if not db.get(Product, p["id"]):
            db.add(new_product(p))
    db.commit()

    if not db.scalar(select(func.count(Order.id))):
        samples = [
            ("Rahim Uddin", "01712345678", "House 7, Road 2, Mirpur 10, Dhaka", "inside_dhaka", "cod", "P01", "L", "Black", 1, "shipped", 2),
            ("Nusrat Jahan", "01898765432", "Kazir Dewri, Chattogram", "outside_dhaka", "bkash", "P05", "M", "Pink", 2, "confirmed", 1),
            ("Rahim Uddin", "01712345678", "House 7, Road 2, Mirpur 10, Dhaka", "inside_dhaka", "cod", "P13", "Free size", "Black", 3, "new", 0),
        ]
        for name, phone, addr, area, pay, pid, size, colour, qty, status, days_ago in samples:
            p = db.get(Product, pid)
            lines = [{"product_id": pid, "name": p.name_en, "size": size, "colour": colour, "quantity": qty, "unit_price": p.price}]
            subtotal, charge, total = quote_total(lines, area)
            db.add(Order(customer_name=name, phone=phone, address=addr, delivery_area=area, payment_method=pay,
                         items=lines, subtotal=subtotal, delivery_charge=charge, total=total, status=status,
                         created_at=now() - timedelta(days=days_ago)))
        db.commit()

    if "--demo" in sys.argv:
        names = ["Rahim Uddin", "Nusrat Jahan", "Tanvir Ahmed", "Farhana Akter", "Sabbir Hossain", "Mim Rahman", "Arif Khan"]
        products = db.scalars(select(Product)).all()
        for days_ago in range(60):
            for _ in range(random.randint(0, 6) + (days_ago < 30)):
                lines = [{"product_id": p.id, "name": p.name_en, "size": random.choice(p.sizes), "colour": random.choice(p.colours),
                          "quantity": random.randint(1, 2), "unit_price": p.price}
                         for p in random.sample(products, random.choice([1, 1, 1, 2]))]
                area = random.choice(["inside_dhaka", "inside_dhaka", "outside_dhaka"])
                subtotal, charge, total = quote_total(lines, area)
                status = "delivered" if days_ago > 5 else random.choice(["new", "confirmed", "shipped"])
                db.add(Order(customer_name=random.choice(names), phone=f"017{random.randint(10000000, 99999999)}", address="Demo address",
                             delivery_area=area, payment_method=random.choice(["cod", "cod", "bkash", "nagad"]), items=lines,
                             subtotal=subtotal, delivery_charge=charge, total=total,
                             status="cancelled" if random.random() < 0.07 else status,
                             created_at=now() - timedelta(days=days_ago, hours=random.randint(0, 12))))
        db.commit()
print("Seeded.")
