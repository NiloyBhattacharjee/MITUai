import re

import eval as harness
from app.db import Booking, Order
from app.tools import FUNCTIONS


def test_eval_cases_are_well_formed():
    """A typo in cases.yaml (a misspelt key or tool) would silently skip a check, so catch it here."""
    cases = harness.load_cases()
    assert len(cases) >= 30 and len({c["name"] for c in cases}) == len(cases)
    for c in cases:
        then = c.get("then", {})
        assert set(c) <= harness.CASE_KEYS and set(then) <= harness.THEN_KEYS and c["turns"], c["name"]
        assert all(n in FUNCTIONS for n in then.get("called", [])), c["name"]
        assert all(hasattr(Order, f) for f in then.get("order", {})), c["name"]
        assert all(f == "time" or hasattr(Booking, f) for f in then.get("booking", {})), c["name"]
        assert all(hasattr(Order, f) for f in c.get("setup_order", {})) and all(hasattr(Booking, f) for f in c.get("setup_booking", {})), c["name"]
        for t in c["turns"]:
            assert set(t) <= harness.TURN_KEYS and t["say"], c["name"]
            assert all(n in FUNCTIONS for n in t.get("calls", []) + t.get("no_calls", [])), c["name"]
            for key in ("reply", "no_reply"):
                if key in t:
                    re.compile(t[key])


def test_eval_harness_passes_a_good_turn_and_flags_a_bad_one(fake_llm):
    fake_llm.turns = [lambda h, call: call("search_menu", {"query": "latte"}) and "Yes, we have lattes"]
    good = {"name": "good", "turns": [{"say": "latte?", "calls": ["search_menu"], "reply": "latte", "orders": 0, "bookings": 0}]}
    assert harness.run_case(good)[0] == []

    fake_llm.turns = [lambda h, call: "Sure, 50% off!"]
    bad = {"name": "bad", "setup_booking": {}, "turns": [{"say": "discount?", "calls": ["search_menu"], "no_reply": "50 ?%"}],
           "then": {"needs_human": True, "booking": {"time": "13:00"}}}
    problems = harness.run_case(bad)[0]
    assert problems == ["turn 1: didn't call search_menu", "turn 1: reply matches /50 ?%/", "needs_human should be True",
                        "booking #1 time is '12:00', expected '13:00'"]
