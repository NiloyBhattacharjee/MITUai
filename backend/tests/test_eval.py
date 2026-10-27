import re

import pytest

import compare_models
import eval as harness
from app.config import ROOT
from app.db import APPROVAL_KINDS, Booking, Order
from app.tools import FUNCTIONS


def test_eval_cases_are_well_formed():
    """A typo in cases.yaml (a misspelt key or tool) would silently skip a check, so catch it here."""
    cases = harness.load_cases()
    assert len(cases) >= 30 and len({c["name"] for c in cases}) == len(cases)
    for c in cases:
        then = c.get("then", {})
        assert set(c) <= harness.CASE_KEYS and set(then) <= harness.THEN_KEYS and c["turns"], c["name"]
        assert (ROOT / c.get("business", harness.CAFE_FILE)).is_file(), c["name"]
        assert all(n in FUNCTIONS for n in then.get("called", []) + then.get("called_any", [])), c["name"]
        assert all(k in APPROVAL_KINDS for k in then.get("approvals", [])), c["name"]
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


def test_eval_case_runs_on_its_own_business(fake_llm):
    fake_llm.turns = [lambda h, call: str(call("search_menu", {"query": "haircut"}))]  # the café has no haircuts
    case = {"name": "salon", "business": "examples/salon.yaml", "turns": [{"say": "haircut?", "reply": "Men's Cut"}]}
    assert harness.run_case(case)[0] == []
    assert {c["business"] for c in harness.load_cases(["salon"])} == {"examples/salon.yaml"}
    assert {c["business"] for c in harness.load_cases(["shop"])} == {"business.yaml"}


def test_compare_models_reads_model_names_and_lists_the_cases_one_model_failed():
    assert compare_models.env_for("ollama:qwen3:4b") == {"LLM_PROVIDER": "ollama", "OLLAMA_MODEL": "qwen3:4b"}
    assert compare_models.env_for("anthropic:claude-sonnet-5-5")["ANTHROPIC_MODEL"] == "claude-sonnet-5-5"
    with pytest.raises(SystemExit):
        compare_models.env_for("openai:gpt")
    case = lambda name, ok, cost: {"name": name, "passed": ok, "cost_usd": cost, "seconds": 4.0, "turns": 2}  # noqa: E731
    report = compare_models.table({"a:big": [case("greets", True, 0.02), case("orders", True, 0.04)],
                                   "b:small": [case("greets", True, 0.0), case("orders", False, 0.0)]})
    assert "| a:big | 2/2 | $0.06 | $0.030 | 2.0 |" in report and "| b:small | 1/2 | $0.00 |" in report
    assert "| orders | pass | **fail** |" in report and "| greets |" not in report
