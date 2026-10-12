import csv
import hmac
import io
import logging
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from time import monotonic, sleep
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select, update

from . import agent, hours, messenger, options, pipeline, tools, whatsapp
from .config import ADMIN_PASSWORD, BUSINESS, CURRENCY, MEDIA_DIR, STAFF, cents
from .db import (BOOKING_STATUSES, ORDER_STATUSES, Approval, Booking, Conversation, LlmUsage, MenuItem, Message, Order, Session,
                 Setting, ToolCall, init_db, now)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
init_db()


def every_minute():
    while True:
        sleep(60)
        try:
            pipeline.remind_unconfirmed()
        except Exception:
            logging.exception("reminders failed")


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=pipeline.resume_unfinished, daemon=True).start()
    threading.Thread(target=every_minute, daemon=True).start()
    yield

app = FastAPI(title=f"{BUSINESS['name']} AI agent", lifespan=lifespan)
app.include_router(whatsapp.router)
app.include_router(messenger.router)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def message_dict(m: Message) -> dict:
    url = f"/media/{m.audio_path}" if m.audio_path else None
    photo = bool(url) and tools.is_photo(url)
    return {"id": m.id, "role": m.role, "text": m.text, "is_voice": m.is_voice, "audio_url": None if photo else url,
            "image_url": url if photo else None, "author": m.author, "created_at": iso(m.created_at)}


def messages_of(db, conv_id: str) -> list[dict]:
    return [message_dict(m) for m in db.scalars(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id))]


@app.get("/api/business")
def business():
    return {"name": BUSINESS["name"], "agent_name": BUSINESS["agent_name"], "currency": CURRENCY}


# --- dashboard login ---------------------------------------------------------------------------

if ADMIN_PASSWORD in ("", "admin", "change-me"):
    raise RuntimeError("Set ADMIN_PASSWORD in .env to a real password before starting the server.")

MAX_WRONG, LOCKOUT_SECONDS = 10, 15 * 60
_wrong: dict[str, list[float]] = {}  # client IP -> times of recent wrong passwords
# ponytail: in-memory, one process; it resets on restart, which only gives a guesser another 10 tries.


def admin(request: Request, x_admin_password: str = Header("")) -> str:
    """The signed-in person's name. Each owner/staff password is different, so the password says who it is.
    10 wrong passwords from one IP lock that IP out for 15 minutes (per IP, so a guesser can't lock the owner out)."""
    ip = request.client.host if request.client else "?"
    recent = [t for t in _wrong.get(ip, []) if t > monotonic() - LOCKOUT_SECONDS]
    if len(recent) >= MAX_WRONG:
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    who = [name for name, pw in STAFF.items() if hmac.compare_digest(x_admin_password.encode(), pw.encode())]
    if not who:
        if x_admin_password:  # a guess, not just a signed-out page loading
            _wrong[ip] = recent + [monotonic()]
        raise HTTPException(401, "wrong password")
    return who[0]


dash = Depends(admin)


# --- simulator (signed in only: every message runs the paid LLM and can place real orders) ------

class SimMessage(BaseModel):
    phone: str = Field(min_length=3, max_length=20)
    text: str = Field(min_length=1, max_length=2000)


@app.post("/api/sim/message", dependencies=[dash])
def sim_message(body: SimMessage, background: BackgroundTasks):
    background.add_task(pipeline.handle_text, f"sim-{body.phone}", "sim", body.phone, body.text)
    return {"ok": True}


@app.post("/api/sim/voice", dependencies=[dash])
async def sim_voice(background: BackgroundTasks, phone: str = Form(min_length=3, max_length=20), file: UploadFile = None):
    data = await file.read()
    if not data or len(data) > 10_000_000:
        raise HTTPException(400, "empty or too large")
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3"}.get(
        (file.content_type or "").split(";")[0], "webm")
    background.add_task(pipeline.handle_voice, f"sim-{phone}", "sim", phone, data, ext)
    return {"ok": True}


@app.post("/api/sim/photo", dependencies=[dash])
async def sim_photo(background: BackgroundTasks, phone: str = Form(min_length=3, max_length=20),
                    caption: str = Form("", max_length=2000), file: UploadFile = None):
    data = await file.read()
    if not data or len(data) > 5_000_000:
        raise HTTPException(400, "empty or too large")
    background.add_task(pipeline.handle_photo, f"sim-{phone}", "sim", phone, data, file.content_type or "", caption)
    return {"ok": True}


@app.get("/api/sim/{phone}/messages", dependencies=[dash])
def sim_messages(phone: str):
    with Session() as db:
        conv = db.get(Conversation, f"sim-{phone}")
        return {"messages": messages_of(db, f"sim-{phone}"), "bot_enabled": conv.bot_enabled if conv else True}


@app.get("/api/me")
def me(who: str = dash):
    return {"name": who}


@app.get("/api/conversations", dependencies=[dash])
def conversations():
    with Session() as db:
        convs = db.scalars(select(Conversation).order_by(Conversation.updated_at.desc())).all()
        last_ids = select(func.max(Message.id)).group_by(Message.conversation_id)
        last = {m.conversation_id: m for m in db.scalars(select(Message).where(Message.id.in_(last_ids)))}
        heard = dict(db.execute(select(Message.conversation_id, func.max(Message.created_at))
                                .where(Message.role == "user").group_by(Message.conversation_id)).all())
        return [{"id": c.id, "channel": c.channel, "phone": c.phone,
                 "bot_enabled": c.bot_enabled, "needs_human": c.needs_human, "handoff_reason": c.handoff_reason,
                 "window_open": pipeline.window_open(c.channel, heard.get(c.id)),
                 "updated_at": iso(c.updated_at), "last_message": message_dict(last[c.id]) if c.id in last else None}
                for c in convs]


@app.get("/api/conversations/{conv_id}/messages", dependencies=[dash])
def conversation_messages(conv_id: str):
    """Each customer message the AI answered gets "ai": the tools it used and what that turn cost."""
    with Session() as db:
        msgs = messages_of(db, conv_id)
        ids = [m["id"] for m in msgs if m["role"] == "user"]
        used = {}
        for message_id, name in db.execute(select(ToolCall.message_id, ToolCall.name).where(ToolCall.message_id.in_(ids)).order_by(ToolCall.id)):
            used.setdefault(message_id, []).append(name)
        cost = dict(db.execute(select(LlmUsage.message_id, func.sum(LlmUsage.cost_usd))
                               .where(LlmUsage.message_id.in_(ids)).group_by(LlmUsage.message_id)).all())
        for m in msgs:
            if m["id"] in used or m["id"] in cost:
                m["ai"] = {"tools": used.get(m["id"], []), "cost_usd": cost.get(m["id"])}
        return msgs


@app.get("/api/messages/{message_id}/steps", dependencies=[dash])
def ai_steps(message_id: int):
    """Full tool calls for one turn, fetched when staff open it (too big to send on every chat poll)."""
    with Session() as db:
        return [{"name": s.name, "args": s.args, "result": s.result, "ms": s.ms}
                for s in db.scalars(select(ToolCall).where(ToolCall.message_id == message_id).order_by(ToolCall.id))]


class Reply(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _get(db, model, key):
    obj = db.get(model, key)
    if not obj:
        raise HTTPException(404)
    return obj


@app.post("/api/conversations/{conv_id}/reply")
def human_reply(conv_id: str, body: Reply, who: str = dash):
    with Session() as db:
        conv = _get(db, Conversation, conv_id)
        if not pipeline.window_open(conv.channel, pipeline.last_customer_message_at(db, conv.id)):
            raise HTTPException(409, "The 24-hour reply window has closed. You can reply once the customer messages again.")
        pipeline.deliver(conv.channel, conv.phone, body.text)  # send first: don't log a reply that never left
        agent.add_message(db, conv, "human", body.text).author = who
        db.commit()
    return {"ok": True}


@app.get("/api/saved-replies", dependencies=[dash])
def saved_replies():
    return BUSINESS.get("saved_replies", [])


class BotToggle(BaseModel):
    enabled: bool


@app.post("/api/conversations/{conv_id}/bot", dependencies=[dash])
def toggle_bot(conv_id: str, body: BotToggle):
    with Session() as db:
        conv = _get(db, Conversation, conv_id)
        conv.bot_enabled = body.enabled
        if body.enabled:  # owner hands the chat back to the bot
            conv.needs_human, conv.handoff_reason = False, None
        db.commit()
    return {"ok": True}


# --- orders and the kitchen --------------------------------------------------------------------

ACTIVE = ["new", "preparing", "ready"]


def order_row(o: Order) -> dict:
    return {"id": o.id, "customer_name": o.customer_name, "phone": o.phone, "fulfilment": o.fulfilment,
            "fulfilment_label": tools.FULFILMENT.get(o.fulfilment, {}).get("label", o.fulfilment), "address": o.address,
            "ready_at": iso(o.ready_at), "items": [tools.line_text(line) for line in o.items], "notes": o.notes, "total": o.total,
            "payment_method": o.payment_method, "status": o.status, "created_at": iso(o.created_at), "conversation_id": o.conversation_id}


@app.get("/api/orders", dependencies=[dash])
def orders(active: bool = False):
    """All orders, newest first (the latest 500; the CSV export has every one), or with active=true what the
    kitchen is working on, soonest first."""
    q = (select(Order).where(Order.status.in_(ACTIVE)).order_by(Order.ready_at) if active
         else select(Order).order_by(Order.created_at.desc()).limit(500))
    with Session() as db:
        return [order_row(o) for o in db.scalars(q)]


def _cell(value) -> str:
    """Customer-typed text could start with = + - @ and run as a formula in Excel; a leading ' keeps it text."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/api/orders/export.csv", dependencies=[dash])
def export_orders():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Order", "Placed", "Ready for", "Customer", "Phone", "Pickup/delivery", "Address", "Items", "Notes",
                "Subtotal", "Fee", "Total", "Payment", "Status"])
    amount = lambda c: f"{c / 100:.2f}"  # noqa: E731
    with Session() as db:
        for o in db.scalars(select(Order).order_by(Order.id)):
            w.writerow([o.id, f"{hours.to_local(o.created_at):%Y-%m-%d %H:%M}", f"{hours.to_local(o.ready_at):%Y-%m-%d %H:%M}",
                        _cell(o.customer_name), o.phone, o.fulfilment, _cell(o.address or ""),
                        _cell("; ".join(tools.line_text(line) for line in o.items)), _cell(o.notes or ""),
                        amount(o.subtotal), amount(o.fee), amount(o.total), o.payment_method, o.status])
    # The BOM makes Excel read the file as UTF-8, so names with accents show correctly.
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="orders.csv"'})


class Status(BaseModel):
    status: str


@app.patch("/api/orders/{order_id}", dependencies=[dash])
def set_order_status(order_id: int, body: Status):
    """The customer is told when it's being made, ready (or on its way) and cancelled."""
    if body.status not in ORDER_STATUSES:
        raise HTTPException(400, f"status must be one of {ORDER_STATUSES}")
    with Session() as db:
        o = _get(db, Order, order_id)
        old, o.status = o.status, body.status
        db.commit()
        note = None
        msgs = BUSINESS["messages"]
        text = msgs.get(f"order_{body.status}_{o.fulfilment}") or msgs.get(f"order_{body.status}")
        if body.status != old and text and (conv := db.get(Conversation, o.conversation_id or "")):
            note = pipeline.send_update(db, conv, text.format(order_id=o.id))
    return {"ok": True, "note": note}


class Settings(BaseModel):
    orders_paused: bool


@app.get("/api/settings", dependencies=[dash])
def get_settings():
    with Session() as db:
        return {"orders_paused": tools.orders_paused(db)}


@app.put("/api/settings", dependencies=[dash])
def put_settings(body: Settings):
    """orders_paused: the assistant stops taking orders (bookings and questions carry on)."""
    with Session() as db:
        db.merge(Setting(key="orders_paused", value=body.orders_paused))
        db.commit()
    return body


# --- bookings ------------------------------------------------------------------------------------

def booking_row(b: Booking) -> dict:
    return {"id": b.id, "customer_name": b.customer_name, "phone": b.phone, "party_size": b.party_size, "at": iso(b.at),
            "notes": b.notes, "status": b.status, "conversation_id": b.conversation_id}


@app.get("/api/bookings", dependencies=[dash])
def bookings(days: int = Query(14, ge=1, le=90)):
    """Bookings from the start of today (café time) for the next `days` days."""
    start = hours.to_utc(datetime.combine(hours.local_now().date(), time()))
    with Session() as db:
        rows = db.scalars(select(Booking).where(Booking.at >= start, Booking.at < start + timedelta(days=days)).order_by(Booking.at))
        return [booking_row(b) for b in rows]


@app.patch("/api/bookings/{booking_id}", dependencies=[dash])
def set_booking_status(booking_id: int, body: Status):
    if body.status not in BOOKING_STATUSES:
        raise HTTPException(400, f"status must be one of {BOOKING_STATUSES}")
    with Session() as db:
        b = _get(db, Booking, booking_id)
        old, b.status = b.status, body.status
        db.commit()
        note = None
        if body.status == "cancelled" and old != "cancelled" and (conv := db.get(Conversation, b.conversation_id or "")):
            text = BUSINESS["messages"]["booking_cancelled"].format(when=hours.label(hours.to_local(b.at)), party_size=b.party_size)
            note = pipeline.send_update(db, conv, text)
    return {"ok": True, "note": note}


# --- approvals ----------------------------------------------------------------------------------

def approval_row(db, a: Approval) -> dict:
    conv, order, d = db.get(Conversation, a.conversation_id), a.order_id and db.get(Order, a.order_id), a.details or {}
    return {"id": a.id, "kind": a.kind, "summary": a.summary, "status": a.status, "note": a.note, "decided_by": a.decided_by,
            "created_at": iso(a.created_at), "decided_at": a.decided_at and iso(a.decided_at), "conversation_id": a.conversation_id,
            "channel": conv and conv.channel, "customer": conv and conv.phone, "order": order and order_row(order),
            "booking": d.get("at") and {**d, "when": hours.label(datetime.fromisoformat(d["at"]))}}


@app.get("/api/approvals", dependencies=[dash])
def approvals():
    """Pending requests, oldest first, then the 30 most recently decided."""
    with Session() as db:
        pending = db.scalars(select(Approval).where(Approval.status == "pending").order_by(Approval.id)).all()
        done = db.scalars(select(Approval).where(Approval.status != "pending").order_by(Approval.decided_at.desc()).limit(30)).all()
        return [approval_row(db, a) for a in [*pending, *done]]


class Decision(BaseModel):
    decision: Literal["approved", "declined"]
    note: str = Field("", max_length=1000)  # staff's message to the customer


@app.post("/api/approvals/{approval_id}")
def decide(approval_id: int, body: Decision, who: str = dash):
    """The customer is told the answer in their chat. An approved big-group booking is booked."""
    note = body.note.strip()
    with Session() as db:
        # Conditional update, so two staff deciding at once can't both act on it.
        done = db.execute(update(Approval).where(Approval.id == approval_id, Approval.status == "pending")
                          .values(status=body.decision, note=note or None, decided_by=who, decided_at=now())).rowcount
        a = _get(db, Approval, approval_id)
        if not done:
            raise HTTPException(409, f"Already {a.status} by {a.decided_by}.")
        msgs = BUSINESS["messages"]
        if body.decision == "approved" and a.kind == "large_booking" and a.details:
            d, at = a.details, datetime.fromisoformat(a.details["at"])
            b = Booking(conversation_id=a.conversation_id, customer_name=d["customer_name"], phone=d["phone"], party_size=d["party_size"],
                        at=hours.to_utc(at), notes=d.get("notes"))
            db.add(b)
            db.flush()
            text = msgs["large_booking_approved"].format(party_size=b.party_size, when=hours.label(at), booking_id=b.id, note=note)
        else:
            text = msgs[f"approval_{body.decision}"].format(summary=a.summary, note=note)
        db.commit()
        conv = db.get(Conversation, a.conversation_id)
        sent = pipeline.send_update(db, conv, text.strip()) if conv else None
        return {**approval_row(db, a), "delivery_note": sent}


# --- menu ----------------------------------------------------------------------------------------

def item_row(i: MenuItem) -> dict:
    photo = tools.item_photo(i.id)
    return {"id": i.id, "name": i.name, "category": i.category, "description": i.description, "price": i.price,
            "options": options.lines(i.options), "dietary": i.dietary, "allergens": i.allergens, "aliases": i.aliases,
            "available": i.available,
            # ?v= so the browser shows a replaced photo
            "photo_url": f"/media/menu/{photo.name}?v={int(photo.stat().st_mtime)}" if photo else None}


@app.get("/api/menu", dependencies=[dash])
def menu():
    with Session() as db:
        return [item_row(i) for i in db.scalars(select(MenuItem).order_by(MenuItem.id))]


Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class ItemFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=1000)
    price: float | None = Field(None, gt=0, le=100_000)  # an amount like 4.50
    options: list[str] | None = None  # one line per group, see options.py
    dietary: list[Tag] | None = None
    allergens: list[Tag] | None = None
    aliases: list[Tag] | None = None  # other names customers use; helps search
    available: bool | None = None

    def apply(self, i: MenuItem):
        data = self.model_dump(exclude_none=True)
        if "options" in data:
            try:
                data["options"] = options.parse(data["options"])
            except ValueError as e:
                raise HTTPException(422, f"Options: {e}")
        if "price" in data:
            data["price"] = cents(data["price"])
        if "category" in data:
            data["category"] = data["category"].lower()
        for k in ("dietary", "allergens", "aliases"):
            if k in data:
                data[k] = list(dict.fromkeys(x.lower() for x in data[k]))  # drop repeats, keep order
        for k, v in data.items():
            setattr(i, k, v)


class NewItem(ItemFields):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    price: float = Field(gt=0, le=100_000)


@app.post("/api/menu", dependencies=[dash])
def add_item(body: NewItem):
    with Session() as db:
        nums = [int(i[1:]) for i in db.scalars(select(MenuItem.id)) if re.fullmatch(r"M\d+", i)]
        i = MenuItem(id=f"M{max(nums, default=0) + 1:02d}", description="", options=[], dietary=[], allergens=[], aliases=[], available=True)
        body.apply(i)
        db.add(i)
        db.commit()
        return item_row(i)


@app.patch("/api/menu/{item_id}", dependencies=[dash])
def edit_item(item_id: str, body: ItemFields):
    with Session() as db:
        i = _get(db, MenuItem, item_id)
        body.apply(i)
        db.commit()
        return item_row(i)


@app.delete("/api/menu/{item_id}", dependencies=[dash])
def delete_item(item_id: str):
    """Past orders keep their item names; an unconfirmed quote with it fails as "unknown item"."""
    with Session() as db:
        i = _get(db, MenuItem, item_id)
        for photo in (MEDIA_DIR / "menu").glob(f"{i.id}.*"):
            photo.unlink()
        db.delete(i)
        db.commit()
    return {"ok": True}


@app.post("/api/menu/{item_id}/photo", dependencies=[dash])
async def upload_photo(item_id: str, file: UploadFile):
    ext = {"image/jpeg": "jpg", "image/png": "png"}.get(file.content_type)  # what WhatsApp accepts
    data = await file.read()
    if not ext or not data or len(data) > 5_000_000:
        raise HTTPException(400, "Upload a JPG or PNG photo under 5 MB.")
    with Session() as db:
        i = _get(db, MenuItem, item_id)
        folder = MEDIA_DIR / "menu"
        folder.mkdir(exist_ok=True)
        for old in folder.glob(f"{i.id}.*"):
            old.unlink()
        (folder / f"{i.id}.{ext}").write_bytes(data)
        return item_row(i)


# --- analytics -----------------------------------------------------------------------------------

def _period(db, start, end) -> dict:
    chat_ids = set(db.scalars(select(Message.conversation_id).where(
        Message.created_at >= start, Message.created_at < end, Message.role == "user")))
    human_ids = set(db.scalars(select(Message.conversation_id).where(Message.role == "human", Message.conversation_id.in_(chat_ids))))
    flagged = set(db.scalars(select(Conversation.id).where(Conversation.id.in_(chat_ids), Conversation.needs_human)))
    orders = db.scalars(select(Order).where(Order.created_at >= start, Order.created_at < end)).all()
    live = [o for o in orders if o.status != "cancelled"]
    booked = db.scalars(select(Booking).where(Booking.created_at >= start, Booking.created_at < end, Booking.status != "cancelled")).all()
    revenue = sum(o.total for o in live)
    ai_cost, ai_tokens = db.execute(select(func.sum(LlmUsage.cost_usd), func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens))
                                    .where(LlmUsage.created_at >= start, LlmUsage.created_at < end)).one()
    customers = {o.conversation_id for o in live} | {b.conversation_id for b in booked}  # chats that ordered or booked
    return {"revenue": revenue, "orders": len(live), "cancelled": len(orders) - len(live),
            "bookings": len(booked), "covers": sum(b.party_size for b in booked),
            "ai_cost_usd": round(ai_cost or 0, 4), "ai_tokens": ai_tokens or 0,
            "avg_order": round(revenue / len(live)) if live else 0, "chats": len(chat_ids),
            "conversion_pct": round(100 * len(customers & chat_ids) / len(chat_ids)) if chat_ids else 0,
            "ai_handled_pct": round(100 * len(chat_ids - human_ids - flagged) / len(chat_ids)) if chat_ids else 0,
            "_orders": orders, "_live": live}


def _count(pairs, labels: dict | None = None, top: int | None = None) -> list[dict]:
    acc = {}
    for key, revenue, n in pairs:
        a = acc.setdefault(key, {"key": key, "label": (labels or {}).get(key, {}).get("label", key), "revenue": 0, "count": 0})
        a["revenue"] += revenue
        a["count"] += n
    return sorted(acc.values(), key=lambda a: -a["revenue"])[:top]


@app.get("/api/analytics", dependencies=[dash])
def analytics(days: int = Query(30, ge=1, le=365)):
    # ponytail: aggregates in Python over the period's orders, fine for a small café; move to SQL GROUP BY past ~100k orders.
    first = hours.local_now().date() - timedelta(days=days - 1)
    utc = lambda d: hours.to_utc(datetime.combine(d, time()))  # noqa: E731
    start, end = utc(first), utc(first + timedelta(days=days))
    with Session() as db:
        cur = _period(db, start, end)
        prev = _period(db, utc(first - timedelta(days=days)), start)
    by_day = {first + timedelta(days=i): {"revenue": 0, "orders": 0} for i in range(days)}
    for o in cur["_live"]:
        d = by_day[hours.to_local(o.created_at).date()]
        d["revenue"] += o.total
        d["orders"] += 1
    live = cur["_live"]
    return {
        "days": days,
        "current": {k: v for k, v in cur.items() if not k.startswith("_")},
        "previous": {k: v for k, v in prev.items() if not k.startswith("_")},
        "daily": [{"date": d.isoformat(), **v} for d, v in by_day.items()],
        "top_items": _count(((line["name"], line["unit_price"] * line["quantity"], line["quantity"]) for o in live for line in o.items), top=5),
        "fulfilment": _count(((o.fulfilment, o.total, 1) for o in live), tools.FULFILMENT),
        "status": [{"status": s, "count": sum(o.status == s for o in cur["_orders"])} for s in ORDER_STATUSES],
    }
