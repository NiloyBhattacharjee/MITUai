from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import notify, pipeline
from app.db import Approval, Booking, Conversation, Message, Order, now
from app.main import app
from app.tools import Ctx, create_order, request_approval

client, H = TestClient(app), {"X-Admin-Password": "pw"}


def chat(db, conv_id="sim-1"):
    db.add(Conversation(id=conv_id, channel="sim", phone=conv_id[4:]))
    db.commit()
    return Ctx(db, db.get(Conversation, conv_id), 1)


def last_text(db, conv_id="sim-1"):
    return db.scalars(select(Message.text).where(Message.conversation_id == conv_id).order_by(Message.id)).all()[-1]


def test_refund_request_waits_for_staff_and_the_customer_hears_back(db, monkeypatch):
    emails = []
    monkeypatch.setattr(notify, "email_owner", lambda subject, body: emails.append(subject))
    c = chat(db)
    o = Order(conversation_id="sim-1", customer_name="A", phone="0412345678", fulfilment="pickup", ready_at=now(), items=[],
              subtotal=500, fee=0, total=500, payment_method="in_person", status="completed")
    db.add(o)
    db.commit()
    assert "No order" in request_approval(chat(db, "sim-2"), "refund", "Refund please", order_id=o.id)["error"]  # not their order
    asked = request_approval(c, "refund", "Refund for order #1: flat white was cold", order_id=o.id)
    assert asked["status"] == "WAITING_FOR_STAFF" and emails == ["Approval needed: refund"]
    assert request_approval(c, "refund", "Refund again?", order_id=o.id)["status"] == "ALREADY_WAITING"
    assert db.get(Conversation, "sim-1").bot_enabled  # unlike a handoff, the assistant keeps chatting

    pending = client.get("/api/approvals", headers=H).json()
    assert pending[0]["summary"].startswith("Refund for order #1") and pending[0]["order"]["total"] == 500
    r = client.post(f"/api/approvals/{asked['approval_id']}", json={"decision": "declined", "note": "We'll remake it next visit."},
                    headers={"X-Admin-Password": "rina-pw"}).json()
    assert r["status"] == "declined" and r["decided_by"] == "Rina" and r["delivery_note"] is None
    assert last_text(db) == "Sorry, the team couldn't approve your request (Refund for order #1: flat white was cold). We'll remake it next visit."
    again = client.post(f"/api/approvals/{asked['approval_id']}", json={"decision": "approved"}, headers=H)
    assert again.status_code == 409 and "declined by Rina" in again.json()["detail"]
    assert client.post(f"/api/approvals/{asked['approval_id']}", json={"decision": "maybe"}, headers=H).status_code == 422


def test_approved_big_group_gets_booked(db, clock):
    c = chat(db)
    tomorrow = (clock + timedelta(days=1)).date().isoformat()
    bad = request_approval(c, "large_booking", "15 people", booking={"party_size": 15, "date": tomorrow, "time": "23:00"})
    assert any("can't book" in p for p in bad["problems"]) and any("phone" in p for p in bad["problems"])
    asked = request_approval(c, "large_booking", "Table for 15 tomorrow 12:00, birthday", booking={
        "customer_name": "Mia Rossi", "phone": "0412 345 678", "party_size": 15, "date": tomorrow, "time": "12:00", "notes": "birthday"})
    assert asked["status"] == "WAITING_FOR_STAFF" and db.scalars(select(Booking)).all() == []

    r = client.post(f"/api/approvals/{asked['approval_id']}", json={"decision": "approved", "note": "See you then!"}, headers=H).json()
    booking = db.scalars(select(Booking)).one()
    assert (booking.party_size, booking.customer_name, booking.notes, booking.conversation_id) == (15, "Mia Rossi", "birthday", "sim-1")
    assert r["booking"]["when"].endswith("12:00")
    assert last_text(db) == f"Good news: your table for 15 on {r['booking']['when']} is booked (booking #{booking.id}). See you then!"


def test_one_reminder_for_an_unanswered_order_summary(db):
    order = {"customer_name": "Olivia", "phone": "0412345678", "fulfilment": "pickup", "pickup_time": "asap",
             "items": [{"item_id": "M04", "quantity": 1, "options": ["Regular"]}]}
    for conv_id in ("sim-quiet", "sim-busy"):
        c = chat(db, conv_id)
        db.add(Message(conversation_id=conv_id, role="user", text="a long black please"))
        db.commit()
        c.turn_id = db.scalar(select(Message.id).where(Message.conversation_id == conv_id))
        create_order(c, False, **order)
    db.add(Message(conversation_id="sim-busy", role="user", text="hmm, let me think"))  # answered the summary
    db.commit()

    pipeline.remind_unconfirmed()
    assert last_text(db, "sim-quiet") == "a long black please"  # too soon: quoted just now

    for conv_id in ("sim-quiet", "sim-busy"):
        conv = db.get(Conversation, conv_id)
        conv.pending_order = {**conv.pending_order, "quoted_at": (now() - timedelta(minutes=11)).isoformat()}
    db.commit()
    pipeline.remind_unconfirmed()
    pipeline.remind_unconfirmed()  # never twice
    texts = db.scalars(select(Message.text).where(Message.conversation_id == "sim-quiet")).all()
    assert texts[-1] == "Just checking: would you like me to place your order ($4.50)? Reply yes to confirm, or tell me what to change."
    assert len(texts) == 2 and last_text(db, "sim-busy") == "hmm, let me think"


def test_saved_replies_for_staff(db):
    assert client.get("/api/saved-replies").status_code == 401
    assert client.get("/api/saved-replies", headers=H).json()[0].startswith("Thanks for your patience")
    assert db.scalars(select(Approval)).all() == []
