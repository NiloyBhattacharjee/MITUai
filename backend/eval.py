"""Run the scripted customer conversations in evals/cases.yaml against the real LLM and check what the agent did.

    python eval.py                  every case
    python eval.py cancel refund    only cases whose name contains one of the words

It uses LLM_PROVIDER and the keys from .env, so it costs real money: roughly $1-3 for all cases on Sonnet, about
double on Opus, free on Ollama. Each case runs on its own throwaway database (your shop.db is never touched) and no
emails are sent. Checks are deterministic: which tools were called, what's in the database, regexes on the reply.
Exits 1 if any case fails, so it can gate a deploy.
"""
import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="mitu-eval-"))  # before importing app: config reads these at import
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp / 'eval.db'}", MEDIA_DIR=str(_tmp / "media"), ALERT_EMAIL="")

import json  # noqa: E402
import re  # noqa: E402

import yaml  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app import agent, tools  # noqa: E402
from app.config import BUSINESS, LLM_PROVIDER, MEDIA_DIR  # noqa: E402
from app.db import Base, Conversation, LlmUsage, Order, Session, engine, new_product  # noqa: E402

CASES = Path(__file__).parent / "evals" / "cases.yaml"
PHONE = "01712345678"
CONV = f"sim-{PHONE}"
PAST_ORDER = {"customer_name": "Karim Hasan", "phone": PHONE, "address": "House 1, Road 2, Banani, Dhaka",
              "delivery_area": "inside_dhaka", "payment_method": "cod", "subtotal": 950, "delivery_charge": 70, "total": 1020,
              "items": [{"product_id": "P04", "name": "Blue Cotton Kurti", "size": "M", "colour": "Blue", "quantity": 1, "unit_price": 950}]}
TURN_KEYS = {"say", "calls", "no_calls", "reply", "no_reply", "orders"}
CASE_KEYS = {"name", "turns", "setup_order", "then"}
THEN_KEYS = {"needs_human", "called", "order"}


def load_cases(words: list[str] = ()) -> list[dict]:
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    return [c for c in cases if not words or any(w.lower() in c["name"].lower() for w in words)]


def fresh_db(setup_order: dict | None):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    (MEDIA_DIR / "products").mkdir(parents=True, exist_ok=True)
    (MEDIA_DIR / "products" / "P01.jpg").write_bytes(b"JPEG")  # so send_product_photo has something to send
    with Session() as db:
        db.add_all(new_product(p) for p in BUSINESS["products"])
        if setup_order is not None:
            db.add(Conversation(id=CONV, channel="sim", phone=PHONE))
            db.add(Order(conversation_id=CONV, **{**PAST_ORDER, **setup_order}))
        db.commit()


def run_case(case: dict) -> tuple[list[str], list[str], float]:
    """(problems, transcript, cost in $). No problems means the case passed."""
    fresh_db(case.get("setup_order"))
    calls, problems, transcript = [], [], []
    real_run_tool = tools.run_tool

    def recording(ctx, name, args):
        result = real_run_tool(ctx, name, args)
        calls.append((name, args, result))
        return result

    tools.run_tool = recording
    try:
        for i, turn in enumerate(case["turns"], 1):
            start = len(calls)
            reply = agent.respond(CONV, "sim", PHONE, turn["say"])
            text = reply.text if reply else ""
            names = [name for name, _, _ in calls[start:]]
            transcript.append(f"  customer: {turn['say']}")
            transcript += [f"    {n}({json.dumps(a, ensure_ascii=False)}) -> {json.dumps(r, ensure_ascii=False, default=str)[:300]}"
                           for n, a, r in calls[start:]]
            transcript.append(f"  mitu: {text}")
            at = f"turn {i}"
            problems += [f"{at}: didn't call {n}" for n in turn.get("calls", []) if n not in names]
            problems += [f"{at}: called {n}" for n in turn.get("no_calls", []) if n in names]
            if "reply" in turn and not re.search(turn["reply"], text, re.I):
                problems.append(f"{at}: reply doesn't match /{turn['reply']}/")
            if "no_reply" in turn and re.search(turn["no_reply"], text, re.I):
                problems.append(f"{at}: reply matches /{turn['no_reply']}/")
            if "orders" in turn:
                with Session() as db:
                    if (n := db.scalar(select(func.count(Order.id)))) != turn["orders"]:
                        problems.append(f"{at}: {n} orders in the database, expected {turn['orders']}")
    finally:
        tools.run_tool = real_run_tool

    then = case.get("then", {})
    problems += [f"never called {n}" for n in then.get("called", []) if n not in {name for name, _, _ in calls}]
    with Session() as db:
        if "needs_human" in then and bool((conv := db.get(Conversation, CONV)) and conv.needs_human) != then["needs_human"]:
            problems.append(f"needs_human should be {then['needs_human']}")
        order = db.get(Order, 1)
        for field, want in then.get("order", {}).items():
            if (got := getattr(order, field, None)) != want:
                problems.append(f"order #1 {field} is {got!r}, expected {want!r}")
        cost = db.scalar(select(func.sum(LlmUsage.cost_usd))) or 0.0
    return problems, transcript, cost


def main(words: list[str]):
    sys.stdout.reconfigure(encoding="utf-8")
    cases = load_cases(words)
    print(f"Running {len(cases)} cases on {LLM_PROVIDER}\n")
    failed, total = 0, 0.0
    for case in cases:
        try:
            problems, transcript, cost = run_case(case)
        except Exception as e:  # one broken case shouldn't hide the rest
            problems, transcript, cost = [f"crashed: {e!r}"], [], 0.0
        total += cost
        print(f"{'PASS' if not problems else 'FAIL'}  {case['name']}  (${cost:.3f})", flush=True)
        if problems:
            failed += 1
            print("\n".join([*(f"  ! {p}" for p in problems), *transcript]) + "\n", flush=True)
    print(f"\n{len(cases) - failed}/{len(cases)} passed, ${total:.2f} total")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
