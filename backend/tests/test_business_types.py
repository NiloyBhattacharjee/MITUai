"""The default shop (business.yaml) next to the café: it counts stock and takes orders for later days."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import add_cafe
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import agent, hours, tenancy
from app.db import Conversation, Setting
from app.main import app
from app.tools import Ctx, cancel_order, create_order, find_item, specs, take_stock

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
client = TestClient(app)


@pytest.fixture
def shop(db):
    with tenancy.scope(add_cafe(db, "nakshi", {"Owner": "shop-pw"}, EXAMPLES.parent / "business.yaml")) as s:
        yield s


def ctx(db, conv_id, turn=1):
    conv = db.get(Conversation, conv_id) or Conversation(id=conv_id, channel="sim", phone="1")
    db.add(conv)
    db.commit()
    return Ctx(db, conv, turn)


def next_day(clock, friday: bool):
    """The next day (from tomorrow) that is, or isn't, a Friday: the shop only opens at 15:00 on Fridays."""
    return next(d for d in (clock.date() + timedelta(days=n) for n in range(1, 8)) if (d.weekday() == 4) == friday)


def order(day, qty=2):
    return {"customer_name": "Nusrat Jahan", "phone": "01712 345678", "fulfilment": "inside_dhaka", "address": "Road 7, Banani",
            "payment_method": "bkash", "pickup_time": f"{day} 15:00", "items": [{"item_id": "P02", "quantity": qty, "options": ["M"]}]}


def test_shop_never_sells_more_than_it_has_and_cancelling_puts_stock_back(db, shop, clock):
    c, day = ctx(db, "nakshi:sim-1"), next_day(clock, friday=False)
    assert "only 3 left" in create_order(c, False, **order(day, qty=4))["problems"][0]
    assert create_order(c, False, **order(day))["status"] == "QUOTE_ONLY_NOT_PLACED"

    find_item(db, "P02").stock = 1  # someone else bought two while this customer was deciding
    db.commit()
    assert "only 1 left" in create_order(Ctx(db, c.conv, 2), True)["problems"][0]
    find_item(db, "P02").stock = 3
    db.commit()
    placed = create_order(Ctx(db, c.conv, 3), True)
    assert placed["status"] == "PLACED" and find_item(db, "P02").stock == 1

    assert take_stock(db, [{"item_id": "P02", "quantity": 2}]) == ["Mustard Embroidered Kurti"]  # the last-one race
    assert find_item(db, "P02").stock == 1
    assert cancel_order(c, placed["order_id"])["status"] == "CANCELLED" and find_item(db, "P02").stock == 3

    sold_out = {**order(day), "items": [{"item_id": "P05", "quantity": 1, "options": ["40"]}]}  # stock: 0
    assert "Men's Cotton Panjabi is sold out" in create_order(c, False, **sold_out)["problems"][0]


def test_shop_takes_orders_for_later_days_inside_its_hours(db, shop, clock):
    c = ctx(db, "nakshi:sim-1")
    day = next_day(clock, friday=False)
    quote = create_order(c, False, **{**order(day), "pickup_time": str(day)})  # a day without a time: from opening
    assert quote["summary"]["ready_at"] == hours.label(datetime.combine(day, datetime.min.time()).replace(hour=10))
    friday = next_day(clock, friday=True)
    assert "On Fridays we're open 15:00-20:00" in create_order(c, False, **{**order(friday), "pickup_time": f"{friday} 11:00"})["problems"][0]
    too_far = clock.date() + timedelta(days=8)
    assert "up to 7 days ahead" in create_order(c, False, **order(too_far))["problems"][0]
    assert {s["name"] for s in specs()} >= {"create_order", "search_menu"}


def test_prompt_only_describes_what_the_business_does(db, shop):
    prompt = agent.system_prompt(False)
    assert "Ordering (pickup or delivery)" in prompt and "online clothing shop" in prompt
    assert "allerg" not in prompt.split("Handoff")[0].lower()  # the shop has no allergen note


def test_dashboard_cancel_returns_stock_and_edits_counts(db, shop, clock):
    H = {"X-Session": "shop-pw"}
    assert client.get("/api/business", headers=H).json()["orders"] is True
    c = ctx(db, "nakshi:sim-1")
    create_order(c, False, **order(next_day(clock, friday=False)))
    order_id = create_order(Ctx(db, c.conv, 2), True)["order_id"]
    stock = lambda: client.get("/api/menu", headers=H).json()[1]["stock"]  # noqa: E731  P02
    assert stock() == 1
    assert client.patch(f"/api/orders/{order_id}", json={"status": "cancelled"}, headers=H).status_code == 200 and stock() == 3
    assert client.patch(f"/api/orders/{order_id}", json={"status": "new"}, headers=H).status_code == 200 and stock() == 1
    client.patch("/api/menu/P02", json={"stock": 0}, headers=H)
    client.patch(f"/api/orders/{order_id}", json={"status": "cancelled"}, headers=H)  # back to 2
    client.patch("/api/menu/P02", json={"stock": 0}, headers=H)
    assert client.patch(f"/api/orders/{order_id}", json={"status": "new"}, headers=H).status_code == 409  # none left to take
    assert client.patch("/api/menu/P02", json={"stock": None}, headers=H).json()["stock"] is None  # stop counting
    assert client.patch("/api/menu/P02", json={"name": "Mustard Kurti"}, headers=H).json()["stock"] is None


def test_seed_never_adds_another_business_menu_to_the_first_one(db, cafe):
    import seed
    assert seed.add_new_items(db, cafe, EXAMPLES.parent / "business.yaml") is None  # the shop's file, but this is the café
    assert not find_item(db, "P01")
    assert seed.add_new_items(db, cafe, EXAMPLES / "cafe.yaml") == 0  # its own file: nothing new


@pytest.mark.parametrize("business", ["cafe", "shop"])
def test_seed_fills_every_kind_of_business_with_local_customers(db, business, request):
    import seed
    from app.db import Order
    request.getfixturevalue(business)
    seed.samples(db, demo=True)
    orders, cfg = db.scalars(select(Order)).all(), tenancy.cfg()
    assert orders and {o.phone[:2] for o in orders} == ({"01"} if business == "shop" else {"04"})  # local numbers in Dhaka
    assert all(o.fulfilment in cfg["fulfilment"] and o.payment_method in cfg["payment_methods"] for o in orders)


def test_dashboard_words_fit_the_business(db, shop):
    words = {pw: client.get("/api/business", headers={"X-Session": pw}).json()["labels"] for pw in ("pw", "shop-pw")}
    assert words == {"pw": {"kitchen": "Kitchen", "menu": "Menu"}, "shop-pw": {"kitchen": "Packing", "menu": "Products"}}


def test_paused_shop_asks_customers_to_try_later(db, shop, clock):
    db.add(Setting(key="orders_paused", value=True))
    db.commit()
    problem = create_order(ctx(db, "nakshi:sim-1"), False, **order(next_day(clock, friday=False)))["problems"][0]
    assert "paused" in problem and "try again later" in problem
