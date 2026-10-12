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


def test_simulator_needs_login_and_guessing_locks_out(fake_llm):
    from app import main
    main._wrong.clear()
    assert client.post("/api/sim/message", json={"phone": "017", "text": "hi"}).status_code == 401
    assert client.get("/api/sim/017/messages").status_code == 401
    assert fake_llm.calls == []  # nothing reached the paid LLM
    assert client.post("/api/sim/message", json={"phone": "017", "text": "hi"}, headers={"X-Admin-Password": "pw"}).status_code == 200

    for _ in range(10):
        assert client.get("/api/me", headers={"X-Admin-Password": "guess"}).status_code == 401
    assert client.get("/api/me", headers={"X-Admin-Password": "pw"}).status_code == 429  # even the right one, from this IP
    main._wrong.clear()


def test_staff_can_see_which_tools_the_ai_used(db, fake_llm):
    from app.agent import respond
    fake_llm.turns = [lambda h, call: call("search_products", {"query": "kurti"}) and "We have kurtis"]
    asked = respond("sim-9", "sim", "9", "kurti ache?")
    h = {"X-Admin-Password": "pw"}
    customer = next(m for m in client.get("/api/conversations/sim-9/messages", headers=h).json() if m["role"] == "user")
    assert customer["ai"]["tools"] == ["search_products"]
    steps = client.get(f"/api/messages/{customer['id']}/steps", headers=h).json()
    assert steps[0]["args"] == {"query": "kurti"} and steps[0]["result"]["results"][0]["id"] == "P04"
    assert asked.text == "We have kurtis"
    assert client.get(f"/api/messages/{customer['id']}/steps").status_code == 401
