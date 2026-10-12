import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import tenancy
from app.db import Conversation, MenuItem, Message, Order, Session, Shop, Staff, ToolCall, now
from app.main import app
from app.tools import find_item
from conftest import add_cafe

client = TestClient(app)
FERN, HARBOUR = {"X-Session": "pw"}, {"X-Session": "harbour-pw"}


def order(name: str, total: int) -> Order:
    return Order(customer_name=name, phone="0412345678", fulfilment="pickup", ready_at=now(), items=[], subtotal=total, fee=0,
                 total=total, payment_method="in_person")


@pytest.fixture
def harbour(db):
    shop = add_cafe(db, "harbour", {"Owner": "harbour-pw"}, whatsapp_phone_number_id="456", page_id="page-2")
    with tenancy.scope(shop):
        db.add(order("Harbour customer", 9900))
        db.add(Conversation(id="harbour:sim-1", channel="sim", phone="1"))
        db.commit()
    db.add(order("Fern customer", 1500))
    db.commit()
    return shop


def test_each_cafe_only_sees_its_own_data(db, harbour):
    theirs = client.get("/api/orders", headers=HARBOUR).json()
    assert [o["customer_name"] for o in theirs] == ["Harbour customer"]
    ours = client.get("/api/orders", headers=FERN).json()
    assert [o["customer_name"] for o in ours] == ["Fern customer"]
    fern_order = ours[0]["id"]
    assert client.patch(f"/api/orders/{fern_order}", json={"status": "cancelled"}, headers=HARBOUR).status_code == 404  # not theirs
    assert db.get(Order, fern_order, populate_existing=True).status == "new"

    assert client.get("/api/analytics?days=7", headers=HARBOUR).json()["current"]["revenue"] == 9900
    assert client.get("/api/analytics?days=7", headers=FERN).json()["current"]["revenue"] == 1500
    assert [c["id"] for c in client.get("/api/conversations", headers=HARBOUR).json()] == ["harbour:sim-1"]
    assert client.get("/api/conversations/harbour:sim-1/messages", headers=FERN).json() == []

    # Both cafés have an M01: changing theirs leaves ours alone.
    assert client.patch("/api/menu/M01", json={"price": 9}, headers=HARBOUR).json()["price"] == 900
    assert find_item(db, "M01").price == 500
    assert client.get("/api/business", headers=HARBOUR).json()["id"] == "harbour"


def test_sums_and_counts_are_per_cafe_too(db, harbour):
    from app.db import LlmUsage
    with tenancy.scope(harbour):
        db.add(LlmUsage(conversation_id="harbour:sim-1", model="claude-sonnet-5-5", cost_usd=5.0))
        db.commit()
        assert db.scalar(select(func.sum(LlmUsage.cost_usd))) == 5.0
        assert db.scalar(select(func.count(Order.id))) == 1
    assert db.scalar(select(func.sum(LlmUsage.cost_usd))) is None
    assert db.scalar(select(func.count(Order.id))) == 1
    assert client.get("/api/analytics?days=7", headers=FERN).json()["current"]["ai_cost_usd"] == 0


def test_ai_steps_of_another_cafe_stay_hidden(db, harbour):
    with tenancy.scope(harbour):
        m = Message(conversation_id="harbour:sim-1", role="user", text="secret")
        db.add(m)
        db.flush()
        db.add(ToolCall(message_id=m.id, name="search_menu", args={}, result={"x": 1}, ms=1))
        db.commit()
    assert client.get(f"/api/messages/{m.id}/steps", headers=FERN).json() == []
    assert len(client.get(f"/api/messages/{m.id}/steps", headers=HARBOUR).json()) == 1


def test_queries_without_a_cafe_in_scope_fail_instead_of_seeing_everything(db):
    with Session() as fresh, tenancy.scope(None), pytest.raises(RuntimeError, match="No café in scope"):
        fresh.scalar(select(func.count(Order.id)))
    with Session() as fresh:  # cafés and staff themselves aren't scoped
        assert fresh.scalar(select(func.count(Shop.id))) == 1 and fresh.scalar(select(func.count(Staff.id))) == 3


def post(path, payload):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return client.post(path, content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def wa(msg_id, number):
    return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": number}, "messages": [
        {"from": "61400000000", "id": msg_id, "type": "text", "text": {"body": "hi"}}]}}]}]}


def test_webhooks_reach_the_cafe_they_were_sent_to(db, harbour, fake_llm, graph):
    assert post("/webhook", wa("wamid.h", "456")).status_code == 200
    assert post("/webhook", wa("wamid.f", "123")).status_code == 200
    assert post("/webhook", wa("wamid.x", "999")).status_code == 200  # no café has this number: dropped
    assert len(fake_llm.calls) == 2
    assert db.get(Conversation, "little-fern:61400000000") is not None
    with tenancy.scope(harbour):
        assert db.get(Conversation, "harbour:61400000000") is not None
    sent_from = {r.url.path.split("/")[2] for r in graph if r.url.path.endswith("/messages") and b'"text"' in r.content}
    assert sent_from == {"456", "123"}  # each café replies from its own number

    fb = {"object": "page", "entry": [{"id": "page-2", "messaging": [
        {"sender": {"id": "psid-9"}, "recipient": {"id": "page-2"}, "message": {"mid": "m.h", "text": "hello"}}]}]}
    assert post("/messenger/webhook", fb).status_code == 200
    with tenancy.scope(harbour):
        assert db.get(Conversation, "harbour:messenger-psid-9").channel == "messenger"


def test_manage_adds_cafes_staff_and_connections(db, tmp_path, capsys):
    import manage
    from conftest import BUSINESS_FILE
    with pytest.raises(SystemExit, match="8 characters"):
        manage.main(["add-shop", "corner-cafe", str(BUSINESS_FILE), "--owner-password", "short"])
    with pytest.raises(SystemExit, match="lowercase"):
        manage.main(["add-shop", "Corner Cafe", str(BUSINESS_FILE), "--owner-password", "long-enough-pw"])
    manage.main(["add-shop", "corner-cafe", str(BUSINESS_FILE), "--owner-password", "long-enough-pw"])
    manage.main(["add-staff", "corner-cafe", "Sam", "sams-password"])
    manage.main(["connect", "corner-cafe", "--whatsapp-number-id", "777", "--page-token", "tok-7"])
    manage.main(["list"])
    assert "corner-cafe" in capsys.readouterr().out
    with Session() as fresh:
        shop = fresh.get(Shop, "corner-cafe")
        assert (shop.whatsapp_phone_number_id, shop.page_token, shop.config["name"]) == ("777", "tok-7", "Little Fern Café")
    with tenancy.scope(shop):
        assert db.scalar(select(func.count(MenuItem.pk))) == 16  # the menu came with it
    r = client.post("/api/login", json={"shop": "corner-cafe", "password": "sams-password"}).json()
    assert r["name"] == "Sam"
    with pytest.raises(SystemExit, match="already exists"):
        manage.main(["add-shop", "corner-cafe", str(BUSINESS_FILE), "--owner-password", "long-enough-pw"])
