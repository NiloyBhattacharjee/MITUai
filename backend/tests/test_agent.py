from sqlalchemy import select

from app.agent import respond
from app.db import Conversation, Order, Product
from app.tools import Ctx, quote_total, search_products

DETAILS = {"customer_name": "Karim", "phone": "+880 1712-345678", "address": "House 1, Road 2, Banani, Dhaka",
           "delivery_area": "inside_dhaka", "payment_method": "cod",
           "items": [{"product_id": "P04", "size": "m", "colour": "blue", "quantity": 2}]}


def test_order_total_includes_delivery():
    lines = [{"unit_price": 950, "quantity": 2}, {"unit_price": 350, "quantity": 1}]
    assert quote_total(lines, "inside_dhaka") == (2250, 70, 2320)
    assert quote_total(lines, "outside_dhaka") == (2250, 130, 2380)


def test_search_bangla_english_banglish(db):
    ctx = Ctx(db, Conversation(id="t", channel="sim", phone="1"), 1)
    for q in ["panjabi", "পাঞ্জাবি", "punjabi", "Panjabi?", "কালো পাঞ্জাবি আছে?"]:
        ids = [r["id"] for r in search_products(ctx, q)["results"]]
        assert "P01" in ids and "P04" not in ids, q
    assert search_products(ctx, "kalo panjabi")["results"][0]["id"] == "P01"
    assert search_products(ctx, "blue kurti")["results"][0]["id"] == "P04"
    assert search_products(ctx, "iphone")["results"] == []


def test_no_order_before_customer_says_yes(db, fake_llm):
    results = []

    def eager_turn(history, call_tool):  # a misbehaving model: quotes and confirms in the same turn
        results.append(call_tool("create_order", {"confirmed": True}))  # nothing quoted yet
        quote = call_tool("create_order", {"confirmed": False, **DETAILS})
        results.append(quote)
        results.append(call_tool("create_order", {"confirmed": True}))  # customer hasn't seen it yet
        return f"Total {quote['summary']['total']} taka. Confirm?"

    def yes_turn(history, call_tool):
        results.append(call_tool("create_order", {"confirmed": True}))
        return "Order placed!"

    fake_llm.turns = [eager_turn, yes_turn]
    respond("sim-01712345678", "sim", "01712345678", "I want 2 blue kurti M, Karim, 01712345678, Banani, COD")
    assert results[0]["error"] and results[2]["error"]
    assert results[1]["summary"]["total"] == 950 * 2 + 70
    assert db.scalars(select(Order)).all() == []

    respond("sim-01712345678", "sim", "01712345678", "yes")
    placed = results[3]
    assert placed["status"] == "PLACED" and placed["total"] == 1970
    order = db.get(Order, placed["order_id"])
    assert order.phone == "01712345678" and order.items[0]["size"] == "M"
    db.expire_all()
    assert db.get(Product, "P04").stock_of("M", "Blue") == 4 - 2  # 14 spread over S, M, L, XL: 4, 4, 3, 3
    assert db.get(Product, "P04").stock_of("S", "Blue") == 4  # other sizes untouched


def test_quote_rejects_out_of_stock_and_bad_input(db):
    ctx = Ctx(db, Conversation(id="t2", channel="sim", phone="1"), 1)
    from app.tools import create_order
    bad = create_order(ctx, False, **{**DETAILS, "phone": "123", "items": [
        {"product_id": "P10", "size": "M", "colour": "White", "quantity": 1}]})
    assert any("stock" in p for p in bad["problems"]) and any("phone" in p for p in bad["problems"])


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


def test_product_photo_goes_out_on_whatsapp(db, fake_llm, graph):
    import json
    from app.config import MEDIA_DIR
    from app.db import Message
    from app.main import message_dict
    (MEDIA_DIR / "products").mkdir(exist_ok=True)
    (MEDIA_DIR / "products" / "P01.jpg").write_bytes(b"JPEGDATA")
    results = []
    fake_llm.turns = [lambda h, call: results.extend([call("send_product_photo", {"product_id": "P01"}),
                                                      call("send_product_photo", {"product_id": "P02"})]) or "Here it is"]
    respond("8801712345678", "whatsapp", "8801712345678", "chobi dekhan")

    assert results[0]["status"] == "SENT" and "error" in results[1]  # P02 has no photo
    sent = [json.loads(r.content) for r in graph if r.url.path.endswith("/messages")]
    assert sent == [{"messaging_product": "whatsapp", "to": "8801712345678", "type": "image",
                     "image": {"id": "media-out", "caption": "Black Cotton Panjabi – ৳1450"}}]
    photo = db.scalars(select(Message).where(Message.audio_path.is_not(None))).one()
    assert message_dict(photo)["image_url"] == "/media/products/P01.jpg" and message_dict(photo)["audio_url"] is None
    assert search_products(Ctx(db, None, 0), "kalo panjabi")["results"][0]["has_photo"]


def test_status_change_messages_customer_within_whatsapp_window(db, graph):
    from datetime import timedelta
    from fastapi.testclient import TestClient
    from app.db import Message, now
    from app.main import app
    client, h = TestClient(app), {"X-Admin-Password": "pw"}

    def order_for(conv_id, channel, last_seen):
        db.add(Conversation(id=conv_id, channel=channel, phone="8801712345678"))
        db.add(Message(conversation_id=conv_id, role="user", text="hi", created_at=now() - last_seen))
        o = Order(conversation_id=conv_id, customer_name="A", phone="01712345678", address="x", delivery_area="inside_dhaka",
                  payment_method="cod", items=[], subtotal=0, delivery_charge=70, total=70)
        db.add(o)
        db.commit()
        return o.id

    sim = order_for("sim-7", "sim", timedelta(days=3))  # simulator has no 24h rule
    r = client.patch(f"/api/orders/{sim}", json={"status": "shipped"}, headers=h).json()
    assert r["note"] is None
    assert f"#{sim} has been shipped" in db.scalars(select(Message).where(Message.conversation_id == "sim-7")).all()[-1].text

    recent = order_for("8801712345678", "whatsapp", timedelta(hours=2))
    assert client.patch(f"/api/orders/{recent}", json={"status": "confirmed"}, headers=h).json()["note"] is None
    assert len(graph) == 1 and f"#{recent} is confirmed" in graph[0].content.decode()

    db.scalars(select(Message).where(Message.conversation_id == "8801712345678", Message.role == "user")).one() \
        .created_at = now() - timedelta(days=2)  # WhatsApp customer went quiet
    db.commit()
    r = client.patch(f"/api/orders/{recent}", json={"status": "delivered"}, headers=h).json()
    assert "24 hours" in r["note"] and len(graph) == 1


def test_new_columns_added_to_existing_database(db):
    from sqlalchemy import inspect, text
    from app.db import engine, init_db
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE orders DROP COLUMN payment_ref"))  # a shop.db from before the column existed
    init_db()
    assert "payment_ref" in {c["name"] for c in inspect(engine).get_columns("orders")}


def test_owner_reply_blocked_after_whatsapp_window(db, graph):
    from datetime import timedelta
    from fastapi.testclient import TestClient
    from app.db import Message, now
    from app.main import app
    client, h = TestClient(app), {"X-Admin-Password": "pw"}
    db.add_all([Conversation(id="880171", channel="whatsapp", phone="880171"),
                Message(conversation_id="880171", role="user", text="hi", created_at=now() - timedelta(hours=25)),
                Conversation(id="sim-9", channel="sim", phone="9")])
    db.commit()
    window = {c["id"]: c["window_open"] for c in client.get("/api/conversations", headers=h).json()}
    assert window == {"880171": False, "sim-9": True}
    r = client.post("/api/conversations/880171/reply", json={"text": "hello"}, headers=h)
    assert r.status_code == 409 and "24-hour" in r.json()["detail"] and graph == []


def test_owner_edits_products_in_dashboard(db):
    from fastapi.testclient import TestClient
    from app.main import app
    client, h = TestClient(app), {"X-Admin-Password": "pw"}
    assert client.get("/api/products").status_code == 401
    # P01 starts as sizes M, L, XL, XXL in Black with 18 spread as 5, 5, 4, 4
    r = client.patch("/api/products/P01", json={"price": 1500, "name_en": "Black Panjabi", "sizes": ["L", "M", "S", "S"]},
                     headers=h).json()
    assert r["price_bdt"] == 1500 and r["name_en"] == "Black Panjabi" and r["sizes"] == ["L", "M", "S"]
    assert r["stock_by_size_colour"] == {"L / Black": 5, "M / Black": 5, "S / Black": 0}  # kept, kept, new; XL/XXL dropped
    r = client.patch("/api/products/P01/stock", json={"size": "S", "colour": "Black", "stock": 7}, headers=h).json()
    assert r["stock_by_size_colour"]["S / Black"] == 7 and r["total_stock"] == 17
    db.expire_all()
    found = search_products(Ctx(db, None, 0), "kalo panjabi")["results"][0]
    assert found["price_bdt"] == 1500 and found["stock_by_size_colour"]["S / Black"] == 7  # the bot sees it
    assert client.patch("/api/products/P01/stock", json={"size": "XL", "colour": "Black", "stock": 1}, headers=h).status_code == 404
    assert client.patch("/api/products/P01/stock", json={"size": "S", "colour": "Black", "stock": -1}, headers=h).status_code == 422
    assert client.patch("/api/products/P01", json={"sizes": []}, headers=h).status_code == 422
    assert client.patch("/api/products/P99", json={"price": 1}, headers=h).status_code == 404

    assert client.delete("/api/products/P03", headers=h).json()["ok"]
    assert "P03" not in [p["id"] for p in client.get("/api/products", headers=h).json()]
    db.expire_all()
    assert db.get(Product, "P03") is None

    bad = client.post("/api/products/P02/photo", files={"file": ("x.gif", b"GIF", "image/gif")}, headers=h)
    assert bad.status_code == 400
    r = client.post("/api/products/P02/photo", files={"file": ("x.png", b"PNGDATA", "image/png")}, headers=h).json()
    assert r["has_photo"] and r["photo_url"].startswith("/media/products/P02.png?v=")
    r = client.post("/api/products/P02/photo", files={"file": ("y.jpg", b"JPEG", "image/jpeg")}, headers=h).json()
    assert r["photo_url"].startswith("/media/products/P02.jpg")  # replaced, not added


def test_bkash_payment_recorded_then_confirmed_by_owner(db, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient
    from app import notify
    from app.db import Message
    from app.main import app
    emails = []
    monkeypatch.setattr(notify, "email_owner", lambda subject, body: emails.append(subject))
    o = Order(conversation_id="sim-3", customer_name="A", phone="01712345678", address="x", delivery_area="inside_dhaka",
              payment_method="bkash", items=[], subtotal=1000, delivery_charge=70, total=1070)
    cod = Order(conversation_id="sim-3", customer_name="A", phone="01712345678", address="x", delivery_area="inside_dhaka",
                payment_method="cod", items=[], subtotal=1000, delivery_charge=70, total=1070)
    db.add_all([o, cod])
    db.commit()
    results = []
    fake_llm.turns = [lambda h, call: results.extend([
        call("record_payment", {"order_id": o.id, "transaction_id": "9 cd4 5xk2 lm"}),
        call("record_payment", {"order_id": o.id, "transaction_id": "hi"}),
        call("record_payment", {"order_id": cod.id, "transaction_id": "9CD45XK2LM"})]) or "Thanks!"]
    respond("sim-3", "sim", "3", "TrxID 9CD45XK2LM")
    assert results[0]["status"] == "RECORDED_NOT_VERIFIED" and "error" in results[1] and "error" in results[2]
    db.refresh(o)
    assert o.payment_ref == "9CD45XK2LM" and not o.paid and emails == [f"Check bKash payment for order #{o.id}"]

    client, h = TestClient(app), {"X-Admin-Password": "pw"}
    assert client.post(f"/api/orders/{o.id}/paid", json={"paid": True}, headers=h).json()["note"] is None
    assert client.post(f"/api/orders/{cod.id}/paid", json={"paid": True}, headers=h).status_code == 400
    texts = db.scalars(select(Message.text).where(Message.conversation_id == "sim-3")).all()
    assert f"payment for order #{o.id}" in texts[-1]
    assert client.post(f"/api/orders/{o.id}/paid", json={"paid": True}, headers=h).json()["note"] is None
    assert len(db.scalars(select(Message.text).where(Message.conversation_id == "sim-3")).all()) == len(texts)  # no repeat


def test_owner_adds_product_in_dashboard(db):
    from fastapi.testclient import TestClient
    from app.main import app
    client, h = TestClient(app), {"X-Admin-Password": "pw"}
    new = {"name_en": " Maroon Silk Panjabi ", "name_bn": "মেরুন সিল্ক পাঞ্জাবি", "category": "Panjabi", "price": 2950,
           "stock": 8, "sizes": ["M", "L"], "colours": ["Maroon"], "aliases": ["meroon panjabi"]}
    r = client.post("/api/products", json=new, headers=h).json()
    assert r["id"] == "P16" and r["name_en"] == "Maroon Silk Panjabi" and r["category"] == "panjabi"
    assert client.post("/api/products", json=new, headers=h).json()["id"] == "P17"
    assert search_products(Ctx(db, None, 0), "meroon panjabi")["results"][0]["id"] == "P16"  # the bot can sell it
    assert client.post("/api/products", json={**new, "price": 0}, headers=h).status_code == 422
    assert client.post("/api/products", json={**new, "sizes": []}, headers=h).status_code == 422
    assert client.post("/api/products", json=new).status_code == 401


def test_owner_emailed_when_an_order_takes_stock_low(db, monkeypatch):
    from app import notify, tools
    sent = []
    monkeypatch.setattr(notify, "email_owner", lambda subject, body: sent.append((subject, body)))
    line = {"product_id": "P04", "name": "Blue Cotton Kurti", "size": "M", "colour": "Blue", "quantity": 1}  # M starts at 4
    assert tools.reserve_stock(db, [line]) is None  # 4 -> 3: crosses the line
    assert tools.reserve_stock(db, [line]) is None  # 3 -> 2: already low, no second email
    assert len(sent) == 1 and "Blue Cotton Kurti (M, Blue): 3 left" in sent[0][1]


def test_customer_cancels_own_unshipped_order(db):
    from app.tools import cancel_order, reserve_stock
    line = {"product_id": "P04", "name": "Blue Cotton Kurti", "size": "M", "colour": "Blue", "quantity": 2, "unit_price": 950}
    reserve_stock(db, [line])
    orders = [Order(conversation_id="sim-1", customer_name="Karim", phone="01712345678", address="Banani", delivery_area="inside_dhaka",
                    payment_method=pm, items=[line], subtotal=1900, delivery_charge=70, total=1970, status=st, payment_ref=ref)
              for pm, st, ref in [("cod", "new", None), ("cod", "shipped", None), ("bkash", "new", "TRX12345678")]]
    db.add_all(orders)
    db.commit()
    mine, stranger = Ctx(db, db.merge(Conversation(id="sim-1", channel="sim", phone="1")), 1), Ctx(db, Conversation(id="sim-2", channel="sim", phone="2"), 1)

    assert "error" in cancel_order(stranger, orders[0].id)  # someone else's chat
    assert "error" in cancel_order(mine, orders[1].id)  # already shipped
    assert cancel_order(mine, orders[2].id)["instruction"] == "Call escalate_to_human."  # paid: needs a refund
    assert cancel_order(mine, orders[0].id)["status"] == "CANCELLED"
    db.expire_all()
    assert db.get(Order, orders[0].id).status == "cancelled"
    assert db.get(Product, "P04").stock_of("M", "Blue") == 4  # back to where it started
