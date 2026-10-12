"""Run the scripted customer conversations in evals/cases.yaml against the real LLM and check what the agent did.

    python eval.py                  every case
    python eval.py cancel booking   only cases whose name contains one of the words

It uses LLM_PROVIDER and the keys from .env, so it costs real money: roughly $1-3 for all cases on Sonnet, about
double on Opus, free on Ollama. Each case runs on its own throwaway database (your shop.db is never touched) and no
emails are sent. The café's clock is set to 10:00 (open every day) whenever you run it. Checks are deterministic:
which tools were called, what's in the database, regexes on the reply. Exits 1 if any case fails.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="mitu-eval-"))  # before importing app: config reads these at import
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp / 'eval.db'}", MEDIA_DIR=str(_tmp / "media"), ALERT_EMAIL="")

import json  # noqa: E402
import re  # noqa: E402

import yaml  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app import agent, hours, shops, tenancy, tools  # noqa: E402
from app.config import BUSINESS_FILE, LLM_PROVIDER  # noqa: E402
from app.db import Approval, Base, Booking, Conversation, LlmUsage, Order, Session, engine, now  # noqa: E402

CASES = Path(__file__).parent / "evals" / "cases.yaml"
PHONE = "0412345678"
CAFE = "eval-cafe"
CONV = f"{CAFE}:sim-{PHONE}"
TURN_KEYS = {"say", "calls", "no_calls", "reply", "no_reply", "orders", "bookings"}
CASE_KEYS = {"name", "turns", "setup_order", "setup_booking", "then"}
THEN_KEYS = {"needs_human", "called", "called_any", "order", "booking", "approvals"}


def café_at_ten():
    """A fixed UTC offset that makes the café's local time 10:00 now, so messages' timestamps and the tools agree."""
    utc = datetime.now(timezone.utc)
    minutes = (10 * 60 - (utc.hour * 60 + utc.minute) + 12 * 60) % (24 * 60) - 12 * 60  # within ±12 hours
    hours.tz = lambda: timezone(timedelta(minutes=minutes))


def load_cases(words: list[str] = ()) -> list[dict]:
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    return [c for c in cases if not words or any(w.lower() in c["name"].lower() for w in words)]


def fresh_db(case: dict):
    """A throwaway database with one café from business.yaml; returns it."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as db:
        shop = shops.create(db, CAFE, shops.read(BUSINESS_FILE), {})
    with tenancy.scope(shop), Session() as db:
        tools.photo_folder().mkdir(parents=True, exist_ok=True)
        (tools.photo_folder() / "M01.jpg").write_bytes(b"JPEG")  # so send_item_photo has something to send
        if "setup_order" in case or "setup_booking" in case:
            db.add(Conversation(id=CONV, channel="sim", phone=PHONE))
        if "setup_order" in case:
            line = {"item_id": "M01", "name": "Flat White", "quantity": 1, "options": ["Regular"], "notes": "", "unit_price": 500}
            db.add(Order(**{"conversation_id": CONV, "customer_name": "Olivia Chen", "phone": PHONE, "fulfilment": "pickup",
                            "payment_method": "in_person", "items": [line], "subtotal": 500, "fee": 0, "total": 500,
                            "ready_at": now() - timedelta(minutes=5), **case["setup_order"]}))
        if "setup_booking" in case:
            tomorrow_noon = datetime.combine(hours.local_now().date() + timedelta(days=1), datetime.min.time()) + timedelta(hours=12)
            db.add(Booking(**{"conversation_id": CONV, "customer_name": "Olivia Chen", "phone": PHONE, "party_size": 2,
                              "at": hours.to_utc(tomorrow_noon), **case["setup_booking"]}))
        db.commit()
    return shop


def run_case(case: dict) -> tuple[list[str], list[str], float]:
    """(problems, transcript, cost in $). No problems means the case passed."""
    with tenancy.scope(fresh_db(case)):
        return _run(case)


def _run(case: dict) -> tuple[list[str], list[str], float]:
    calls, problems, transcript = [], [], []
    real_run_tool = tools.run_tool

    def recording(ctx, name, args):
        result = real_run_tool(ctx, name, args)
        calls.append((name, args, result))
        return result

    def count(model):
        with Session() as db:
            return db.scalar(select(func.count(model.id)))

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
            for key, model in (("orders", Order), ("bookings", Booking)):
                if key in turn and (n := count(model)) != turn[key]:
                    problems.append(f"{at}: {n} {key} in the database, expected {turn[key]}")
    finally:
        tools.run_tool = real_run_tool

    then = case.get("then", {})
    used = {name for name, _, _ in calls}
    problems += [f"never called {n}" for n in then.get("called", []) if n not in used]
    if "called_any" in then and not used & set(then["called_any"]):
        problems.append(f"called none of {then['called_any']}")
    with Session() as db:
        if "needs_human" in then and bool((conv := db.get(Conversation, CONV)) and conv.needs_human) != then["needs_human"]:
            problems.append(f"needs_human should be {then['needs_human']}")
        for key, model in (("order", Order), ("booking", Booking)):
            row = db.get(model, 1)
            for field, want in then.get(key, {}).items():
                got = row and (f"{hours.to_local(row.at):%H:%M}" if field == "time" else getattr(row, field, None))
                if got != want:
                    problems.append(f"{key} #1 {field} is {got!r}, expected {want!r}")
        kinds = sorted(a.kind for a in db.scalars(select(Approval)))
        if "approvals" in then and kinds != sorted(then["approvals"]):
            problems.append(f"approval requests {kinds}, expected {sorted(then['approvals'])}")
        cost = db.scalar(select(func.sum(LlmUsage.cost_usd))) or 0.0
    return problems, transcript, cost


def main(words: list[str]):
    sys.stdout.reconfigure(encoding="utf-8")
    café_at_ten()
    cases = load_cases(words)
    print(f"Running {len(cases)} cases on {LLM_PROVIDER}, café time {hours.local_now():%a %H:%M}\n")
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
