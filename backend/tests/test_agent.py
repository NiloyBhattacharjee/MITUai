from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import options
from app.agent import build_history, respond
from app.db import Booking, Conversation, MenuItem, Message, Order, Setting
from app.main import app
from app.tools import Ctx, book_table, cancel_booking, cancel_order, check_availability, create_order, saved_details, search_menu

client, H = TestClient(app), {"X-Admin-Password": "pw"}
ORDER = {"customer_name": "Olivia Chen", "phone": "0412 345 678", "fulfilment": "pickup", "pickup_time": "asap",
         "items": [{"item_id": "M01", "quantity": 2, "options": ["large", "Oat"]}]}  # (5.00 + 0.80 + 0.80) x 2


def ctx(db, conv_id="sim-1", turn=1):
    conv = db.get(Conversation, conv_id) or Conversation(id=conv_id, channel="sim", phone="1")
    db.add(conv)
    db.commit()
    return Ctx(db, conv, turn)


def test_options_parse_print_and_check_choices():
    groups = options.parse(["Size*: Regular, Large +0.80", "Milk: Full cream, Oat +0.80", "Extras (any): Extra shot +0.60, Decaf"])
    assert options.lines(groups) == ["Size*: Regular, Large +0.80", "Milk: Full cream, Oat +0.80", "Extras (any): Extra shot +0.60, Decaf"]
    assert options.choose(groups, ["oat", "LARGE", "Decaf", "Extra shot"]) == (["Large", "Oat", "Extra shot", "Decaf"], 220, [])
    _, _, problems = options.choose(groups, ["Oat", "Full cream", "Banana"])
    assert any("Size is required" in p for p in problems) and any("Milk: choose only one" in p for p in problems)
    assert any('"Banana" isn\'t an option' in p for p in problems)
    with pytest.raises(ValueError):
        options.parse(["no colon here"])


def test_search_finds_menu_items_by_name_alias_and_typo(db):
    c = ctx(db)
    for q, want in [("flat white", "M01"), ("avo toast", "M08"), ("big brekkie", "M09"), ("capuccino", "M03"), ("Fresh OJ please", "M07")]:
        assert want in [r["id"] for r in search_menu(c, q)["results"]], q
    assert search_menu(c, "iphone")["results"] == []
    tart = search_menu(c, "lemon tart")["results"][0]
    assert tart["id"] == "M16" and tart["available"] is False and tart["price"] == "$9.00"


def test_no_order_before_customer_says_yes(db, fake_llm):
    results = []

    def eager_turn(history, call):  # a misbehaving model: quotes and confirms in the same turn
        results.append(call("create_order", {"confirmed": True}))  # nothing quoted yet
        quote = call("create_order", {"confirmed": False, **ORDER})
        results.append(quote)
        results.append(call("create_order", {"confirmed": True}))  # customer hasn't seen it yet
        return f"That's {quote['summary']['total']}. Confirm?"

    def yes_turn(history, call):
        results.append(call("create_order", {"confirmed": True}))
        return "Order placed!"

    fake_llm.turns = [eager_turn, yes_turn]
    respond("sim-0412345678", "sim", "0412345678", "2 large oat flat whites for pickup asap, Olivia, 0412 345 678")
    assert results[0]["error"] and results[2]["error"]
    assert results[1]["summary"]["total"] == "$13.20" and results[1]["summary"]["items"] == ["2× Flat White (Large, Oat): $13.20"]
    assert db.scalars(select(Order)).all() == []

    respond("sim-0412345678", "sim", "0412345678", "yes")
    placed = results[3]
    assert placed["status"] == "PLACED" and placed["total"] == "$13.20"
    order = db.get(Order, placed["order_id"])
    assert order.phone == "0412345678" and order.items[0]["options"] == ["Large", "Oat"] and order.total == 1320


def test_quote_rejects_what_the_kitchen_cant_do(db):
    c = ctx(db)
    bad = create_order(c, False, **{**ORDER, "phone": "123", "pickup_time": "23:30", "fulfilment": "delivery", "items": [
        {"item_id": "M16", "quantity": 1},  # sold out
        {"item_id": "M09", "quantity": 1},  # Big Breakfast needs the eggs choice
        {"item_id": "M01", "quantity": 1, "options": ["Large", "Whipped cream"]}]})["problems"]
    for expected in ["Lemon Tart is sold out", "Eggs is required", "Whipped cream", "phone", "address is needed", "can't have it ready at 23:30"]:
        assert any(expected in p for p in bad), expected
    assert "earliest" in create_order(c, False, **{**ORDER, "pickup_time": "10:05"})["problems"][0]  # prep takes 15 minutes
    assert create_order(c, False, **{**ORDER, "pickup_time": "12:30"})["summary"]["ready_at"].endswith("12:30")

    db.add(Setting(key="orders_paused", value=True))
    db.commit()
    assert "paused" in create_order(c, False, **ORDER)["problems"][0]


def test_confirming_checks_again(db, fake_llm):
    c = ctx(db, turn=1)
    assert create_order(c, False, **ORDER)["status"] == "QUOTE_ONLY_NOT_PLACED"
    db.get(MenuItem, "M01").available = False  # sold out while the customer was deciding
    db.commit()
    later = Ctx(db, c.conv, 2)
    assert "sold out" in create_order(later, True)["problems"][0] and db.scalars(select(Order)).all() == []
    db.get(MenuItem, "M01").available, db.get(MenuItem, "M01").price = True, 550  # back, but dearer
    db.commit()
    changed = create_order(later, True)
    assert changed["status"] == "QUOTE_CHANGED_NOT_PLACED" and changed["summary"]["total"] == "$14.20"
    assert create_order(Ctx(db, c.conv, 3), True)["status"] == "PLACED"  # yes to the new price, in a later message


def test_customer_cancels_only_own_orders_the_kitchen_hasnt_started(db):
    mine, stranger = ctx(db, "sim-1"), ctx(db, "sim-2")
    o = [Order(conversation_id="sim-1", customer_name="A", phone="0412345678", fulfilment="pickup", ready_at=db_now(),
               items=[], subtotal=0, fee=0, total=0, payment_method="in_person", status=s) for s in ("new", "preparing")]
    db.add_all(o)
    db.commit()
    assert "error" in cancel_order(stranger, o[0].id)
    assert "already started" in cancel_order(mine, o[1].id)["error"]
    assert cancel_order(mine, o[0].id)["status"] == "CANCELLED" and db.get(Order, o[0].id).status == "cancelled"


def db_now():
    from app.db import now
    return now()


def test_bookings_respect_hours_and_seats(db, clock):
    c = ctx(db)
    tomorrow = (clock + timedelta(days=1)).date().isoformat()
    assert check_availability(c, tomorrow, "12:00", 4)["available"]
    assert "can't book" in check_availability(c, tomorrow, "23:00", 2)["problems"][0]
    assert "passed" in check_availability(c, clock.date().isoformat(), "08:00", 2)["problems"][0]
    big = check_availability(c, tomorrow, "12:00", 12)
    assert not big["available"] and big["instruction"] == "Call escalate_to_human for this group."

    booked = book_table(c, "Liam", "0412 345 678", 4, tomorrow, "12:00", "high chair")
    assert booked["status"] == "BOOKED" and booked["party_size"] == 4
    assert book_table(c, "Liam", "0412345678", 4, tomorrow, "12:00")["status"] == "ALREADY_BOOKED"  # asked twice
    for i in range(4):  # fill the room: 4 + 4 x 8 = 36 of 40 seats
        book_table(ctx(db, f"sim-{i + 10}"), "Guest", "0400000000", 8, tomorrow, "12:00")
    full = check_availability(c, tomorrow, "12:15", 6)
    assert not full["available"] and full["other_times_that_day"] and "12:15" not in full["other_times_that_day"]

    assert "error" in cancel_booking(ctx(db, "sim-99"), booked["booking_id"])  # someone else's
    assert cancel_booking(c, booked["booking_id"])["status"] == "CANCELLED"
    assert db.get(Booking, booked["booking_id"]).status == "cancelled"


def test_saved_details_only_from_the_same_chat(db):
    c = ctx(db, "61412345678")
    book_table(c, "Priya Sharma", "0412345678", 2, (db_now() + timedelta(days=2)).date().isoformat(), "12:00")
    back = saved_details(c)
    assert back["customer_name"] == "Priya Sharma" and back["phone"] == "0412345678"
    assert "customer_name" not in saved_details(ctx(db, "61499999999"))


def test_customer_messages_carry_their_local_time(db):
    from app import hours
    db.add(Conversation(id="sim-t", channel="sim", phone="t"))
    m = Message(conversation_id="sim-t", role="user", text="table for 2 tomorrow?", created_at=hours.to_utc(hours.local_now()))
    db.add_all([m, Message(conversation_id="sim-t", role="assistant", text="Sure!")])
    db.commit()
    history = build_history([m])
    now = hours.local_now()
    assert history[0]["text"] == f"[{now:%a} {now.day} {now:%b %Y} 10:00] table for 2 tomorrow?"


def test_handoff_stops_bot(db, fake_llm):
    fake_llm.turns = [lambda h, call: call("escalate_to_human", {"reason": "asked for a person"}) and ""]
    reply = respond("sim-1", "sim", "1", "I want to talk to a real person")
    assert reply.text  # falls back to the configured handoff message
    conv = db.get(Conversation, "sim-1")
    db.refresh(conv)
    assert conv.needs_human and not conv.bot_enabled
    assert respond("sim-1", "sim", "1", "hello?") is None
    assert len(fake_llm.calls) == 1


def test_owner_emailed_once_per_handoff(db, monkeypatch):
    from app import notify
    sent = []
    monkeypatch.setattr(notify, "email_owner", lambda subject, body: sent.append(subject))
    conv = Conversation(id="sim-5", channel="sim", phone="5")
    db.add(conv)
    db.commit()
    notify.flag_for_human(db, conv, "angry customer")
    notify.flag_for_human(db, conv, "Bot error, please check this chat")  # still waiting on a human: no second email
    assert sent == ["Mitu needs a human: 5"] and conv.handoff_reason == "Bot error, please check this chat"


def test_menu_photo_goes_out_on_whatsapp(db, fake_llm, graph):
    import json
    from app.config import MEDIA_DIR
    from app.main import message_dict
    (MEDIA_DIR / "menu").mkdir(exist_ok=True)
    (MEDIA_DIR / "menu" / "M01.jpg").write_bytes(b"JPEGDATA")
    results = []
    fake_llm.turns = [lambda h, call: results.extend([call("send_item_photo", {"item_id": "M01"}),
                                                      call("send_item_photo", {"item_id": "M02"})]) or "Here it is"]
    respond("61412345678", "whatsapp", "61412345678", "can I see the flat white?")

    assert results[0]["status"] == "SENT" and "error" in results[1]  # M02 has no photo
    sent = [json.loads(r.content) for r in graph if r.url.path.endswith("/messages")]
    assert sent == [{"messaging_product": "whatsapp", "to": "61412345678", "type": "image",
                     "image": {"id": "media-out", "caption": "Flat White – $5.00"}}]
    photo = db.scalars(select(Message).where(Message.audio_path.is_not(None))).one()
    assert message_dict(photo)["image_url"] == "/media/menu/M01.jpg" and message_dict(photo)["audio_url"] is None
    assert search_menu(ctx(db), "flat white")["results"][0]["has_photo"]


def test_status_change_tells_the_customer_within_the_whatsapp_window(db, graph):
    from app.db import now

    def order_for(conv_id, channel, last_seen, way="pickup"):
        db.add(Conversation(id=conv_id, channel=channel, phone="61412345678"))
        db.add(Message(conversation_id=conv_id, role="user", text="hi", created_at=now() - last_seen))
        o = Order(conversation_id=conv_id, customer_name="A", phone="0412345678", fulfilment=way, address="x", ready_at=now(),
                  items=[], subtotal=0, fee=0, total=0, payment_method="in_person")
        db.add(o)
        db.commit()
        return o.id

    sim = order_for("sim-7", "sim", timedelta(days=3))  # the simulator has no 24h rule
    assert client.patch(f"/api/orders/{sim}", json={"status": "ready"}, headers=H).json()["note"] is None
    assert f"#{sim} is ready to collect" in db.scalars(select(Message).where(Message.conversation_id == "sim-7")).all()[-1].text

    recent = order_for("61412345678", "whatsapp", timedelta(hours=2), way="delivery")
    assert client.patch(f"/api/orders/{recent}", json={"status": "ready"}, headers=H).json()["note"] is None
    assert len(graph) == 1 and f"#{recent} is on its way" in graph[0].content.decode()
    assert client.patch(f"/api/orders/{recent}", json={"status": "completed"}, headers=H).json()["note"] is None
    assert len(graph) == 1  # no message configured for completed

    db.scalars(select(Message).where(Message.conversation_id == "61412345678", Message.role == "user")).one() \
        .created_at = now() - timedelta(days=2)  # the customer went quiet
    db.commit()
    r = client.patch(f"/api/orders/{recent}", json={"status": "cancelled"}, headers=H).json()
    assert "24 hours" in r["note"] and len(graph) == 1


def test_new_columns_added_to_existing_database(db):
    from sqlalchemy import inspect, text
    from app.db import engine, init_db
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE orders DROP COLUMN notes"))  # a database from before the column existed
    init_db()
    assert "notes" in {c["name"] for c in inspect(engine).get_columns("orders")}


def test_refuses_the_old_clothing_shop_database(db):
    from sqlalchemy import text
    from app.db import engine, init_db
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE variants (product_id TEXT)"))
    with pytest.raises(RuntimeError, match="clothing-shop"):
        init_db()


def test_owner_reply_blocked_after_whatsapp_window(db, graph):
    from app.db import now
    db.add_all([Conversation(id="614171", channel="whatsapp", phone="614171"),
                Message(conversation_id="614171", role="user", text="hi", created_at=now() - timedelta(hours=25)),
                Conversation(id="sim-9", channel="sim", phone="9")])
    db.commit()
    window = {c["id"]: c["window_open"] for c in client.get("/api/conversations", headers=H).json()}
    assert window == {"614171": False, "sim-9": True}
    r = client.post("/api/conversations/614171/reply", json={"text": "hello"}, headers=H)
    assert r.status_code == 409 and "24-hour" in r.json()["detail"] and graph == []


def test_owner_edits_the_menu_in_the_dashboard(db):
    assert client.get("/api/menu").status_code == 401
    r = client.patch("/api/menu/M01", json={"price": 5.5, "name": "Flat White", "available": False,
                                            "options": ["Size*: Regular, Large +1", "Milk: Full cream, Oat +0.9"]}, headers=H).json()
    assert r["price"] == 550 and not r["available"] and r["options"] == ["Size*: Regular, Large +1.00", "Milk: Full cream, Oat +0.90"]
    db.expire_all()
    found = search_menu(ctx(db), "flat white")["results"][0]
    assert found["price"] == "$5.50" and not found["available"] and "Oat (+$0.90)" in found["options"][1]  # the bot sees it
    bad = client.patch("/api/menu/M01", json={"options": ["Milk Oat"]}, headers=H)
    assert bad.status_code == 422 and "Options" in bad.json()["detail"]
    assert client.patch("/api/menu/M01", json={"price": 0}, headers=H).status_code == 422
    assert client.patch("/api/menu/M99", json={"price": 1}, headers=H).status_code == 404

    new = {"name": " Matcha Latte ", "category": "Tea", "price": 6.2, "options": ["Milk: Full cream, Oat +0.80"],
           "dietary": ["Vegetarian"], "aliases": ["matcha", "green tea latte"]}
    r = client.post("/api/menu", json=new, headers=H).json()
    assert r["id"] == "M17" and r["name"] == "Matcha Latte" and r["category"] == "tea" and r["price"] == 620 and r["dietary"] == ["vegetarian"]
    assert search_menu(ctx(db), "green tea latte")["results"][0]["id"] == "M17"  # the bot can sell it
    assert client.post("/api/menu", json={**new, "price": 0}, headers=H).status_code == 422

    assert client.delete("/api/menu/M03", headers=H).json()["ok"]
    db.expire_all()
    assert db.get(MenuItem, "M03") is None
    assert client.post("/api/menu/M02/photo", files={"file": ("x.gif", b"GIF", "image/gif")}, headers=H).status_code == 400
    r = client.post("/api/menu/M02/photo", files={"file": ("x.png", b"PNGDATA", "image/png")}, headers=H).json()
    assert r["photo_url"].startswith("/media/menu/M02.png?v=")
    r = client.post("/api/menu/M02/photo", files={"file": ("y.jpg", b"JPEG", "image/jpeg")}, headers=H).json()
    assert r["photo_url"].startswith("/media/menu/M02.jpg")  # replaced, not added
