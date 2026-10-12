from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import Booking, Order, now
from app.main import app

client = TestClient(app)


def order(total, status="new", days_ago=0, way="pickup", conv=None):
    line = {"item_id": "M08", "name": "Smashed Avo on Sourdough", "quantity": 1, "options": [], "notes": "", "unit_price": total}
    when = now() - timedelta(days=days_ago)
    return Order(customer_name="A", phone="0412345678", fulfilment=way, items=[line], subtotal=total, fee=0, total=total,
                 status=status, payment_method="in_person", ready_at=when, created_at=when, conversation_id=conv)


def test_analytics_totals_exclude_cancelled_and_split_periods(db):
    db.add_all([order(1000), order(2000, way="delivery"), order(5000, status="cancelled"), order(3000, days_ago=10)])
    db.add_all([Booking(customer_name="B", phone="0412345678", party_size=4, at=now() + timedelta(days=1)),
                Booking(customer_name="C", phone="0412345678", party_size=2, at=now() + timedelta(days=1), status="cancelled")])
    db.commit()
    assert client.get("/api/analytics").status_code == 401

    r = client.get("/api/analytics?days=7", headers={"X-Session": "pw"}).json()
    assert r["current"] == {**r["current"], "revenue": 3000, "orders": 2, "cancelled": 1, "avg_order": 1500, "bookings": 1, "covers": 4}
    assert r["previous"]["revenue"] == 3000
    assert len(r["daily"]) == 7 and sum(d["revenue"] for d in r["daily"]) == 3000
    assert [(f["key"], f["label"]) for f in r["fulfilment"]] == [("delivery", "Delivery"), ("pickup", "Pickup")]
    assert r["top_items"][0] == {"key": "Smashed Avo on Sourdough", "label": "Smashed Avo on Sourdough", "revenue": 3000, "count": 2}
    assert {s["status"]: s["count"] for s in r["status"]}["cancelled"] == 1


def test_ai_cost_recorded_per_call_and_shown_on_overview(db, monkeypatch):
    from app import llm
    from app.agent import respond

    class TwoCallLLM:  # one tool round-trip = two API calls
        def run(self, system, history, tools, call_tool, usage=None):
            usage.append(llm._usage("claude-sonnet-5-5", 4000, 200, read=1000))
            usage.append(llm._usage("claude-sonnet-5-5", 4500, 100, write=2000))
            return "Yes, we have it!"

    monkeypatch.setattr(llm, "_llm", TwoCallLLM())
    respond("sim-8", "sim", "8", "do you have oat milk?")
    # (8500 in * $2 + 300 out * $10 + 1000 cache read * $0.20 + 2000 cache write * $2.50) / 1M
    expected = (8500 * 2 + 300 * 10 + 1000 * 0.2 + 2000 * 2.5) / 1_000_000
    r = client.get("/api/analytics?days=7", headers={"X-Session": "pw"}).json()["current"]
    assert abs(r["ai_cost_usd"] - round(expected, 4)) < 1e-9 and r["ai_tokens"] == 8800
    assert llm.cost_usd(llm._usage("claude-opus-5-5", 1_000_000)) == 4  # longest name wins over claude-opus-5
    assert llm.cost_usd(llm._usage("ollama:qwen3:4b", 5000, 500)) == 0 and llm.cost_usd(llm._usage("mystery", 1)) is None
