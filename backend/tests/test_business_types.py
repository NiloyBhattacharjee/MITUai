"""The kinds of business besides the café: the default shop (business.yaml) that counts stock and takes orders for
later days, and a salon that books appointments with the right person."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import add_cafe
from fastapi.testclient import TestClient

from app import agent, hours, tenancy
from app.db import Conversation
from app.main import app
from app.tools import (Ctx, appointment_times, book_appointment, cancel_booking, cancel_order, create_order, find_item, my_bookings,
                       run_tool, specs, take_stock)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
client = TestClient(app)


@pytest.fixture
def shop(db):
    with tenancy.scope(add_cafe(db, "nakshi", {"Owner": "shop-pw"}, EXAMPLES.parent / "business.yaml")) as s:
        yield s


@pytest.fixture
def salon(db):
    with tenancy.scope(add_cafe(db, "bloom", {"Owner": "salon-pw"}, EXAMPLES / "salon.yaml")) as s:
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
    assert not {s["name"] for s in specs()} & {"book_table", "book_appointment", "my_bookings"}


def test_salon_offers_free_times_and_never_double_books_a_person(db, salon, clock):
    c, other = ctx(db, "bloom:sim-1"), ctx(db, "bloom:sim-2")
    day = clock.date() + timedelta(days=1)
    (opens, closes), = hours.periods(day)
    first, two_hours = f"{opens:%H:%M}", f"{opens + timedelta(hours=2):%H:%M}"

    free = appointment_times(c, "S05", str(day))["free_times"]  # Full Colour, 120 minutes: Ava and Mei do it
    assert free[first] == ["Ava", "Mei"] and max(free) == f"{closes - timedelta(hours=2):%H:%M}"
    assert book_appointment(c, "Zoe Park", "0412 345 678", "S05", str(day), first)["with"] == "Ava"
    assert book_appointment(other, "Ali Khan", "0412 000 111", "S05", str(day), first)["with"] == "Mei"
    full = book_appointment(ctx(db, "bloom:sim-3"), "Sam Lee", "0412 000 222", "S05", str(day), first)
    assert full["error"] == "That time isn't free." and full["other_times_that_day"][0] == two_hours

    half_past = f"{opens + timedelta(minutes=30):%H:%M}"
    assert "error" in book_appointment(other, "Sam Lee", "0412 000 222", "S02", str(day), half_past, provider="Ava")  # Ava is colouring
    assert book_appointment(other, "Sam Lee", "0412 000 222", "S02", str(day), half_past)["with"] == "Leo"
    assert "Leo doesn't do Full Colour" in appointment_times(c, "S05", str(day), provider="Leo")["problems"][0]

    mine = my_bookings(c)["bookings"]
    assert [(b["service"], b["with"]) for b in mine] == [("Full Colour", "Ava")]
    assert cancel_booking(c, mine[0]["booking_id"])["status"] == "CANCELLED"
    assert appointment_times(c, "S05", str(day))["free_times"][first] == ["Ava"]  # Mei is still booked

    assert "passed" in appointment_times(c, "S01", str(clock.date() - timedelta(days=1)))["problems"][0]
    assert all(t >= f"{clock:%H:%M}" for t in appointment_times(c, "S02", str(clock.date()))["free_times"])  # none before now
    assert "Unknown tool" in run_tool(c, "create_order", {})["error"]  # a salon doesn't take orders


def test_prompt_only_describes_what_the_business_does(db, salon):
    prompt = agent.system_prompt(False)
    assert "Appointments:" in prompt and "hair and beauty salon" in prompt
    assert "Ordering" not in prompt and "Bookings:" not in prompt and "allerg" not in prompt.split("Handoff")[0].lower()


def test_cafe_prompt_keeps_ordering_and_tables(db):
    prompt = agent.system_prompt(False)
    assert "Ordering (today only" in prompt and "Bookings:" in prompt and "Appointments:" not in prompt


def test_dashboard_cancel_returns_stock_and_edits_counts(db, shop, clock):
    H = {"X-Session": "shop-pw"}
    assert client.get("/api/business", headers=H).json().items() >= {"orders": True, "tables": False, "appointments": False}.items()
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


def test_salon_cant_send_a_big_table_booking_to_the_team(db, salon):
    assert "kind must be one of" in run_tool(ctx(db, "bloom:sim-1"), "request_approval", {"kind": "large_booking", "summary": "12 people"})["error"]


def test_seed_never_adds_another_business_menu_to_the_first_one(db, cafe):
    import seed
    assert seed.add_new_items(db, cafe, EXAMPLES.parent / "business.yaml") is None  # the shop's file, but this is the café
    assert not find_item(db, "P01")
    assert seed.add_new_items(db, cafe, EXAMPLES / "cafe.yaml") == 0  # its own file: nothing new


@pytest.mark.parametrize("business", ["cafe", "shop", "salon"])
def test_seed_fills_every_kind_of_business_and_never_double_books_a_person(db, business, request):
    import seed
    from sqlalchemy import select

    from app.db import Booking, Order
    request.getfixturevalue(business)
    seed.samples(db, demo=True)
    orders, bookings = db.scalars(select(Order)).all(), db.scalars(select(Booking)).all()
    cfg = tenancy.cfg()
    assert bool(orders) == ("fulfilment" in cfg)
    assert all(o.fulfilment in cfg["fulfilment"] and o.payment_method in cfg["payment_methods"] for o in orders)
    assert bool([b for b in bookings if not b.provider]) == ("reservations" in cfg)
    appointments = sorted((b for b in bookings if b.provider and b.status != "cancelled"), key=lambda b: (b.provider, b.at))
    assert bool(appointments) == ("appointments" in cfg)
    for a, b in zip(appointments, appointments[1:]):
        assert a.provider != b.provider or a.at + timedelta(minutes=a.minutes) <= b.at, f"{a.provider} double-booked at {b.at}"


def test_dashboard_words_fit_the_business(db, shop, salon):
    words = {pw: client.get("/api/business", headers={"X-Session": pw}).json()["labels"] for pw in ("pw", "shop-pw", "salon-pw")}
    assert words == {"pw": {"kitchen": "Kitchen", "menu": "Menu"}, "shop-pw": {"kitchen": "Packing", "menu": "Products"},
                     "salon-pw": {"kitchen": "Kitchen", "menu": "Services"}}
