from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import Order, now
from app.main import app

client = TestClient(app)


def order(total, status="new", days_ago=0, pay="cod", conv=None):
    line = {"product_id": "P01", "name": "Black Cotton Panjabi", "size": "L", "colour": "Black", "quantity": 1, "unit_price": total - 70}
    return Order(customer_name="A", phone="01712345678", address="x", delivery_area="inside_dhaka", payment_method=pay,
                 items=[line], subtotal=total - 70, delivery_charge=70, total=total, status=status,
                 created_at=now() - timedelta(days=days_ago), conversation_id=conv)


def test_analytics_totals_exclude_cancelled_and_split_periods(db):
    db.add_all([order(1000), order(2000, pay="bkash"), order(5000, status="cancelled"), order(3000, days_ago=10)])
    db.commit()
    assert client.get("/api/analytics").status_code == 401

    r = client.get("/api/analytics?days=7", headers={"X-Admin-Password": "pw"}).json()
    assert r["current"] == {**r["current"], "revenue": 3000, "orders": 2, "cancelled": 1, "avg_order": 1500}
    assert r["previous"]["revenue"] == 3000
    assert len(r["daily"]) == 7 and sum(d["revenue"] for d in r["daily"]) == 3000
    assert [p["key"] for p in r["payment"]] == ["bkash", "cod"]
    assert r["top_products"][0] == {"key": "Black Cotton Panjabi", "label": "Black Cotton Panjabi", "revenue": 2860, "count": 2}
    assert {s["status"]: s["count"] for s in r["status"]}["cancelled"] == 1


def test_cancel_returns_stock_and_reopen_takes_it_back(db):
    from app.db import Variant
    o = order(1520)
    db.add(o)
    db.commit()
    stock = lambda: db.get(Variant, ("P01", "L", "Black"), populate_existing=True).stock  # noqa: E731
    start, h = stock(), {"X-Admin-Password": "pw"}

    assert client.patch(f"/api/orders/{o.id}", json={"status": "cancelled"}, headers=h).status_code == 200
    assert stock() == start + 1
    assert client.patch(f"/api/orders/{o.id}", json={"status": "cancelled"}, headers=h).status_code == 200
    assert stock() == start + 1  # cancelling twice doesn't double-count
    assert client.patch(f"/api/orders/{o.id}", json={"status": "new"}, headers=h).status_code == 200
    assert stock() == start

    client.patch(f"/api/orders/{o.id}", json={"status": "cancelled"}, headers=h)
    db.get(Variant, ("P01", "L", "Black")).stock = 0
    db.commit()
    r = client.patch(f"/api/orders/{o.id}", json={"status": "shipped"}, headers=h)
    assert r.status_code == 409 and "out of stock" in r.json()["detail"]
    assert stock() == 0 and db.get(Order, o.id, populate_existing=True).status == "cancelled"


def test_ai_cost_recorded_per_call_and_shown_on_overview(db, monkeypatch):
    from app import llm
    from app.agent import respond

    class TwoCallLLM:  # one tool round-trip = two API calls
        def run(self, system, history, tools, call_tool, usage=None):
            usage.append(llm._usage("claude-sonnet-5-5", 4000, 200, read=1000))
            usage.append(llm._usage("claude-sonnet-5-5", 4500, 100, write=2000))
            return "Ji, ache!"

    monkeypatch.setattr(llm, "_llm", TwoCallLLM())
    respond("sim-8", "sim", "8", "panjabi ache?")
    # (8500 in * $2 + 300 out * $10 + 1000 cache read * $0.20 + 2000 cache write * $2.50) / 1M
    expected = (8500 * 2 + 300 * 10 + 1000 * 0.2 + 2000 * 2.5) / 1_000_000
    r = client.get("/api/analytics?days=7", headers={"X-Admin-Password": "pw"}).json()["current"]
    assert abs(r["ai_cost_usd"] - round(expected, 4)) < 1e-9 and r["ai_tokens"] == 8800
    assert llm.cost_usd(llm._usage("claude-opus-5-5", 1_000_000)) == 4  # longest name wins over claude-opus-5
    assert llm.cost_usd(llm._usage("ollama:qwen3:4b", 5000, 500)) == 0 and llm.cost_usd(llm._usage("mystery", 1)) is None
