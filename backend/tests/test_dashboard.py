import csv
import io

from fastapi.testclient import TestClient

from app.db import Conversation, Message, Order
from app.main import app

client = TestClient(app)


def test_staff_sign_in_with_their_own_password_and_replies_are_signed(db):
    assert client.get("/api/me", headers={"X-Admin-Password": "pw"}).json() == {"name": "Owner"}
    assert client.get("/api/me", headers={"X-Admin-Password": "rina-pw"}).json() == {"name": "Rina"}
    assert client.get("/api/me", headers={"X-Admin-Password": "nope"}).status_code == 401
    assert client.get("/api/orders", headers={"X-Admin-Password": "karim-pw"}).status_code == 200

    db.add(Conversation(id="sim-4", channel="sim", phone="4"))
    db.commit()
    assert client.post("/api/conversations/sim-4/reply", json={"text": "Ami Rina, help korchi"},
                       headers={"X-Admin-Password": "rina-pw"}).status_code == 200
    msgs = client.get("/api/conversations/sim-4/messages", headers={"X-Admin-Password": "pw"}).json()
    assert msgs[-1]["role"] == "human" and msgs[-1]["author"] == "Rina"


def test_orders_export_opens_in_excel_with_bangla_and_no_formulas(db):
    db.add(Order(customer_name="=HYPERLINK(\"http://evil\")", phone="01712345678", address="ধানমন্ডি, ঢাকা",
                 delivery_area="inside_dhaka", payment_method="bkash", paid=True, payment_ref="9CD45XK2LM", status="shipped",
                 items=[{"product_id": "P01", "name": "Black Cotton Panjabi", "size": "L", "colour": "Black", "quantity": 2, "unit_price": 1450}],
                 subtotal=2900, delivery_charge=70, total=2970))
    db.commit()
    assert client.get("/api/orders/export.csv").status_code == 401
    r = client.get("/api/orders/export.csv", headers={"X-Admin-Password": "pw"})
    assert r.headers["content-disposition"] == 'attachment; filename="orders.csv"'
    assert r.content.startswith("﻿".encode())  # BOM: Excel reads UTF-8, Bangla shows
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Order", "Date", "Customer"]
    row = dict(zip(rows[0], rows[1]))
    assert row["Customer"] == "'=HYPERLINK(\"http://evil\")"  # stays text, never runs
    assert row["Address"] == "ধানমন্ডি, ঢাকা" and row["Items"] == "2x Black Cotton Panjabi (L, Black)"
    assert (row["Total"], row["Paid"], row["TrxID"], row["Status"]) == ("2970", "yes", "9CD45XK2LM", "shipped")
