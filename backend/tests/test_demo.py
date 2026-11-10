"""The public demo: anyone can chat with one demo business, without signing in, within limits that cap the AI bill."""

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.db import Conversation, Shop

client = TestClient(app=main.app)
VISITOR = "visitor-abc12345"


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(config, "DEMO_SHOP", "little-fern")
    monkeypatch.setattr(config, "DEMO_PER_VISITOR", 3)
    monkeypatch.setattr(config, "DEMO_PER_DAY", 5)
    main._demo_used.clear()
    yield
    main._demo_used.clear()


def say(text, visitor=VISITOR):
    return client.post("/api/demo/message", json={"visitor": visitor, "text": text})


def test_demo_is_off_unless_a_demo_business_is_set(db, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "DEMO_SHOP", "")
    assert client.get("/api/demo").status_code == 404
    assert say("hi").status_code == 404
    assert fake_llm.calls == []  # nothing reached the paid LLM


def test_visitor_chats_with_the_demo_business_without_signing_in(db, fake_llm, demo):
    fake_llm.turns = [lambda history, call_tool: "Hi! What can I get you?"]
    info = client.get("/api/demo").json()
    assert info["name"] == "Little Fern Café" and info["agent_name"]

    assert say("hello").status_code == 200
    conv = db.get(Conversation, f"little-fern:demo-{VISITOR}")
    assert conv is not None and conv.shop_id == "little-fern"  # the chat belongs to the demo business
    msgs = client.get(f"/api/demo/{VISITOR}/messages").json()["messages"]
    assert [(m["role"], m["text"]) for m in msgs] == [("user", "hello"), ("assistant", "Hi! What can I get you?")]
    assert set(msgs[0]) == {"role", "text", "is_voice", "created_at"}  # nothing internal goes to the public page


def test_each_visitor_gets_a_few_messages_and_the_day_has_a_cap(db, fake_llm, demo):
    for _ in range(3):
        assert say("hi").status_code == 200
    assert say("hi").status_code == 429  # this visitor is done
    assert say("hi", visitor="visitor-other999").status_code == 429  # a new id from the same IP doesn't reset it
    assert len(fake_llm.calls) == 3

    main._demo_used.pop("ip:testclient")  # as if other people were chatting
    for v in ("visitor-aaaa1111", "visitor-bbbb2222"):
        assert say("hi", visitor=v).status_code == 200
    assert say("hi", visitor="visitor-cccc3333").status_code == 429  # 5 for the whole day
    assert len(fake_llm.calls) == 5


def test_visitor_ids_and_messages_are_checked(db, fake_llm, demo):
    assert say("hi", visitor="../x").status_code == 422
    assert say("x" * 600).status_code == 422
    assert client.get("/api/demo/..%2Fx/messages").status_code in (404, 422)
    assert fake_llm.calls == []


def test_demo_business_that_does_not_exist_is_off(db, fake_llm, demo, monkeypatch):
    monkeypatch.setattr(config, "DEMO_SHOP", "nobody")
    assert db.get(Shop, "nobody") is None
    assert client.get("/api/demo").status_code == 404
    assert say("hi").status_code == 404


def test_demo_handoff_tells_the_visitor(db, fake_llm, demo):
    fake_llm.turns = [lambda history, call_tool: call_tool("escalate_to_human", {"reason": "wants a person"}) and "Someone will reply."]
    assert say("can I talk to a person?").status_code == 200
    assert client.get(f"/api/demo/{VISITOR}/messages").json()["handed_off"] is True


def test_voice_notes_count_against_the_same_limits(db, fake_llm, demo, monkeypatch):
    from app import pipeline
    heard = []
    monkeypatch.setattr(pipeline, "handle_voice", lambda conv_id, channel, phone, data, ext: heard.append((conv_id, ext)))
    for _ in range(3):
        r = client.post("/api/demo/voice", data={"visitor": VISITOR}, files={"file": ("v.webm", b"audio", "audio/webm")})
        assert r.status_code == 200
    assert heard == [(f"little-fern:demo-{VISITOR}", "webm")] * 3
    assert say("hi").status_code == 429


def test_the_demo_business_file_is_a_sydney_shop_that_cannot_sell_out(db):
    from pathlib import Path
    from app import tenancy, tools
    from app.db import MenuItem
    from tests.conftest import add_cafe
    shop = add_cafe(db, "demo", {"Owner": "demo-pw"}, Path(__file__).resolve().parents[2] / "examples" / "demo-sydney.yaml")
    with tenancy.scope(shop):
        assert tools.sells()
        assert shop.config["currency"] == "$" and shop.config["timezone"] == "Australia/Sydney"
        assert all(i.stock is None for i in db.query(MenuItem))  # visitors can't sell the demo out
