import csv
import io

from fastapi.testclient import TestClient

from app.db import Conversation, Message, Order
from app.main import app

client = TestClient(app)


def test_staff_sign_in_with_their_own_password_and_replies_are_signed(db):
    from app import main
    main._wrong.clear()
    r = client.post("/api/login", json={"shop": " Little-Fern ", "password": "rina-pw"}).json()
    assert r["name"] == "Rina" and r["shop"] == "little-fern"
    rina = {"X-Session": r["token"]}
    assert client.get("/api/me", headers=rina).json() == {"name": "Rina", "shop": "little-fern"}
    assert client.post("/api/login", json={"shop": "little-fern", "password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"shop": "elsewhere", "password": "rina-pw"}).status_code == 401
    assert client.get("/api/me", headers={"X-Session": "made-up"}).status_code == 401

    db.add(Conversation(id="sim-4", channel="sim", phone="4"))
    db.commit()
    assert client.post("/api/conversations/sim-4/reply", json={"text": "Hi, Rina here, happy to help"}, headers=rina).status_code == 200
    msgs = client.get("/api/conversations/sim-4/messages", headers={"X-Session": "pw"}).json()
    assert msgs[-1]["role"] == "human" and msgs[-1]["author"] == "Rina"

    assert client.post("/api/logout", headers=rina).status_code == 200
    assert client.get("/api/me", headers=rina).status_code == 401
    main._wrong.clear()


def test_orders_export_opens_in_excel_with_accents_and_no_formulas(db):
    from app.db import now
    db.add(Order(customer_name="=HYPERLINK(\"http://evil\")", phone="0412345678", fulfilment="delivery", address="12 Rue Émile, Newtown",
                 payment_method="in_person", status="completed", ready_at=now(), notes="Ring the bell",
                 items=[{"item_id": "M01", "name": "Flat White", "quantity": 2, "options": ["Large", "Oat"], "notes": "extra hot", "unit_price": 660}],
                 subtotal=1320, fee=600, total=1920))
    db.commit()
    assert client.get("/api/orders/export.csv").status_code == 401
    r = client.get("/api/orders/export.csv", headers={"X-Session": "pw"})
    assert r.headers["content-disposition"] == 'attachment; filename="orders.csv"'
    assert r.content.startswith("\ufeff".encode())  # BOM: Excel reads UTF-8, accents show
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:4] == ["Order", "Placed", "Ready for", "Customer"]
    row = dict(zip(rows[0], rows[1]))
    assert row["Customer"] == "'=HYPERLINK(\"http://evil\")"  # stays text, never runs
    assert row["Address"] == "12 Rue Émile, Newtown" and row["Items"] == "2× Flat White (Large, Oat) – extra hot"
    assert (row["Subtotal"], row["Fee"], row["Total"], row["Status"]) == ("13.20", "6.00", "19.20", "completed")


def test_kitchen_sees_active_orders_soonest_first_and_can_pause_orders(db):
    from datetime import timedelta
    from app.db import now
    h = {"X-Session": "pw"}
    for minutes, status in [(30, "new"), (10, "preparing"), (5, "completed"), (20, "ready")]:
        db.add(Order(customer_name=status, phone="0412345678", fulfilment="pickup", payment_method="in_person", status=status,
                     ready_at=now() + timedelta(minutes=minutes), items=[], subtotal=0, fee=0, total=0))
    db.commit()
    assert [o["customer_name"] for o in client.get("/api/orders?active=true", headers=h).json()] == ["preparing", "ready", "new"]

    assert client.get("/api/settings", headers=h).json() == {"orders_paused": False}
    assert client.put("/api/settings", json={"orders_paused": True}, headers=h).status_code == 200
    assert client.get("/api/settings", headers=h).json() == {"orders_paused": True}
    assert client.put("/api/settings", json={"orders_paused": True}).status_code == 401


def test_staff_see_bookings_and_cancelling_tells_the_customer(db, clock):
    from datetime import timedelta
    from sqlalchemy import select
    from app import hours
    from app.db import Booking
    h = {"X-Session": "pw"}
    db.add(Conversation(id="sim-b", channel="sim", phone="b"))
    soon, later = hours.to_utc(clock + timedelta(hours=2)), hours.to_utc(clock + timedelta(days=20))
    db.add_all([Booking(conversation_id="sim-b", customer_name="Liam", phone="0412345678", party_size=4, at=soon),
                Booking(customer_name="Far", phone="0412345678", party_size=2, at=later)])
    db.commit()
    rows = client.get("/api/bookings?days=14", headers=h).json()
    assert [b["customer_name"] for b in rows] == ["Liam"]
    r = client.patch(f"/api/bookings/{rows[0]['id']}", json={"status": "cancelled"}, headers=h).json()
    assert r["note"] is None
    last = db.scalars(select(Message).where(Message.conversation_id == "sim-b")).all()[-1].text
    assert "booking for 4" in last and "12:00" in last
    assert client.patch(f"/api/bookings/{rows[0]['id']}", json={"status": "gone"}, headers=h).status_code == 400


def test_simulator_needs_login_and_guessing_locks_out(db, fake_llm):
    from app import main
    main._wrong.clear()
    assert client.post("/api/sim/message", json={"phone": "017", "text": "hi"}).status_code == 401
    assert client.get("/api/sim/017/messages").status_code == 401
    assert fake_llm.calls == []  # nothing reached the paid LLM
    assert client.post("/api/sim/message", json={"phone": "017", "text": "hi"}, headers={"X-Session": "pw"}).status_code == 200
    assert db.get(Conversation, "little-fern:sim-017") is not None  # the simulator chat belongs to this café

    for _ in range(10):
        assert client.post("/api/login", json={"shop": "little-fern", "password": "guess"}).status_code == 401
    assert client.post("/api/login", json={"shop": "little-fern", "password": "pw"}).status_code == 429  # even the right one, from this IP
    main._wrong.clear()


def test_staff_can_see_which_tools_the_ai_used(db, fake_llm):
    from app.agent import respond
    fake_llm.turns = [lambda h, call: call("search_menu", {"query": "latte"}) and "We have lattes"]
    asked = respond("sim-9", "sim", "9", "do you do lattes?")
    h = {"X-Session": "pw"}
    customer = next(m for m in client.get("/api/conversations/sim-9/messages", headers=h).json() if m["role"] == "user")
    assert customer["ai"]["tools"] == ["search_menu"]
    steps = client.get(f"/api/messages/{customer['id']}/steps", headers=h).json()
    assert steps[0]["args"] == {"query": "latte"} and steps[0]["result"]["results"][0]["id"] == "M02"
    assert asked.text == "We have lattes"
    assert client.get(f"/api/messages/{customer['id']}/steps").status_code == 401
