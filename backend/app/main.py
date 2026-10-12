import csv
import hmac
import io
import logging
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from time import monotonic
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select

from . import agent, messenger, pipeline, tools, whatsapp
from .config import ADMIN_PASSWORD, BUSINESS, MEDIA_DIR, STAFF
from .db import ORDER_STATUSES, Conversation, LlmUsage, Message, Order, Product, Session, ToolCall, Variant, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
init_db()


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=pipeline.resume_unfinished, daemon=True).start()
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
    return {"name": BUSINESS["name"], "agent_name": BUSINESS["agent_name"]}


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
        return [{"id": c.id, "channel": c.channel, "phone": c.phone, "language": c.language,
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


@app.get("/api/orders", dependencies=[dash])
def orders():
    with Session() as db:
        return [{"id": o.id, "customer_name": o.customer_name, "phone": o.phone, "address": o.address,
                 "delivery_area": o.delivery_area, "payment_method": o.payment_method, "items": o.items,
                 "total": o.total, "status": o.status, "paid": bool(o.paid), "payment_ref": o.payment_ref,
                 "created_at": iso(o.created_at), "conversation_id": o.conversation_id}
                for o in db.scalars(select(Order).order_by(Order.created_at.desc()))]


def _cell(value) -> str:
    """Customer-typed text could start with = + - @ and run as a formula in Excel; a leading ' keeps it text."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/api/orders/export.csv", dependencies=[dash])
def export_orders():
    tz = ZoneInfo(BUSINESS.get("timezone", "Asia/Dhaka"))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Order", "Date", "Customer", "Phone", "Address", "Delivery area", "Items", "Subtotal", "Delivery charge",
                "Total", "Payment", "Paid", "TrxID", "Status"])
    with Session() as db:
        for o in db.scalars(select(Order).order_by(Order.id)):
            items = "; ".join(f"{l['quantity']}x {l['name']} ({l['size']}, {l['colour']})" for l in o.items)
            w.writerow([o.id, o.created_at.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                        _cell(o.customer_name), o.phone, _cell(o.address), o.delivery_area, _cell(items), o.subtotal,
                        o.delivery_charge, o.total, o.payment_method,
                        "" if o.payment_method == "cod" else ("yes" if o.paid else "no"), _cell(o.payment_ref or ""), o.status])
    # The BOM makes Excel read the file as UTF-8, so Bangla names show correctly.
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="orders.csv"'})


class OrderStatus(BaseModel):
    status: str


@app.patch("/api/orders/{order_id}", dependencies=[dash])
def set_order_status(order_id: int, body: OrderStatus):
    if body.status not in ORDER_STATUSES:
        raise HTTPException(400, f"status must be one of {ORDER_STATUSES}")
    with Session() as db:
        o = _get(db, Order, order_id)
        old = o.status
        if body.status == "cancelled" and old != "cancelled":
            tools.release_stock(db, o.items)
        elif old == "cancelled" and body.status != "cancelled" and (short := tools.reserve_stock(db, o.items)):
            raise HTTPException(409, f"{short} is out of stock, so order #{order_id} can't be reopened.")
        o.status = body.status
        db.commit()
        note = None
        text = BUSINESS["messages"].get(f"order_{body.status}")
        if body.status != old and text and (conv := db.get(Conversation, o.conversation_id or "")):
            note = pipeline.send_update(db, conv, text.format(order_id=o.id))
    return {"ok": True, "note": note}


class Paid(BaseModel):
    paid: bool


@app.post("/api/orders/{order_id}/paid", dependencies=[dash])
def set_paid(order_id: int, body: Paid):
    with Session() as db:
        o = _get(db, Order, order_id)
        if o.payment_method not in ("bkash", "nagad"):
            raise HTTPException(400, "Only bKash and Nagad orders are paid in advance.")
        newly_paid = body.paid and not o.paid
        o.paid = body.paid
        db.commit()
        note = None
        if newly_paid and (conv := db.get(Conversation, o.conversation_id or "")):
            note = pipeline.send_update(db, conv, BUSINESS["messages"]["payment_received"].format(order_id=o.id))
    return {"ok": True, "note": note}


# --- products ------------------------------------------------------------------------------

def product_row(p: Product) -> dict:
    photo = tools.product_photo(p.id)
    return {**tools.product_dict(p), "category": p.category, "aliases": p.aliases,
            "variants": [{"size": v.size, "colour": v.colour, "stock": v.stock} for v in p.variants],
            # ?v= so the browser shows a replaced photo
            "photo_url": f"/media/products/{photo.name}?v={int(photo.stat().st_mtime)}" if photo else None}


@app.get("/api/products", dependencies=[dash])
def products():
    with Session() as db:
        return [product_row(p) for p in db.scalars(select(Product).order_by(Product.id))]


Size = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Colour = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class ProductFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name_en: str | None = Field(None, min_length=1, max_length=200)
    name_bn: str | None = Field(None, max_length=200)
    category: str | None = Field(None, min_length=1, max_length=50)
    price: int | None = Field(None, gt=0, le=1_000_000)
    sizes: list[Size] | None = Field(None, min_length=1)
    colours: list[Colour] | None = Field(None, min_length=1)
    aliases: list[str] | None = None  # other names customers type (Banglish, misspellings); helps search

    def apply(self, p: Product):
        data = {k: v for k, v in self.model_dump(exclude_none=True).items() if k in ProductFields.model_fields}
        for k in ("sizes", "colours"):
            if k in data:
                data[k] = list(dict.fromkeys(data[k]))  # drop repeats, keep order
        if "category" in data:
            data["category"] = data["category"].lower()
        for k, v in data.items():
            setattr(p, k, v)


class NewProduct(ProductFields):
    name_en: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    price: int = Field(gt=0, le=1_000_000)
    sizes: list[Size] = Field(min_length=1)
    colours: list[Colour] = Field(min_length=1)
    stock: int = Field(ge=0, le=100_000)  # starting stock for each size x colour


@app.post("/api/products", dependencies=[dash])
def add_product(body: NewProduct):
    with Session() as db:
        nums = [int(i[1:]) for i in db.scalars(select(Product.id)) if re.fullmatch(r"P\d+", i)]
        p = Product(id=f"P{max(nums, default=0) + 1:02d}", name_bn="", aliases=[])
        body.apply(p)
        p.sync_variants(new_stock=body.stock)
        db.add(p)
        db.commit()
        return product_row(p)


@app.patch("/api/products/{product_id}", dependencies=[dash])
def edit_product(product_id: str, body: ProductFields):
    """Rename, reprice, change sizes/colours: new combinations start at 0 stock, removed ones are dropped."""
    with Session() as db:
        p = _get(db, Product, product_id)
        body.apply(p)
        p.sync_variants()
        db.commit()
        return product_row(p)


class StockEdit(BaseModel):
    size: str
    colour: str
    stock: int = Field(ge=0, le=100_000)


@app.patch("/api/products/{product_id}/stock", dependencies=[dash])
def edit_stock(product_id: str, body: StockEdit):
    with Session() as db:
        _get(db, Variant, (product_id, body.size, body.colour)).stock = body.stock
        db.commit()
        return product_row(_get(db, Product, product_id))


@app.delete("/api/products/{product_id}", dependencies=[dash])
def delete_product(product_id: str):
    """Past orders keep their item names; an unconfirmed quote for it fails as "out of stock"."""
    with Session() as db:
        p = _get(db, Product, product_id)
        for photo in (MEDIA_DIR / "products").glob(f"{p.id}.*"):
            photo.unlink()
        db.delete(p)
        db.commit()
    return {"ok": True}


@app.post("/api/products/{product_id}/photo", dependencies=[dash])
async def upload_photo(product_id: str, file: UploadFile):
    ext = {"image/jpeg": "jpg", "image/png": "png"}.get(file.content_type)  # what WhatsApp accepts
    data = await file.read()
    if not ext or not data or len(data) > 5_000_000:
        raise HTTPException(400, "Photo must be a JPG or PNG under 5 MB.")
    with Session() as db:
        p = _get(db, Product, product_id)
        folder = MEDIA_DIR / "products"
        folder.mkdir(exist_ok=True)
        for old in folder.glob(f"{p.id}.*"):
            old.unlink()
        (folder / f"{p.id}.{ext}").write_bytes(data)
        return product_row(p)


def _period(db, start, end) -> dict:
    chat_ids = set(db.scalars(select(Message.conversation_id).where(
        Message.created_at >= start, Message.created_at < end, Message.role == "user")))
    human_ids = set(db.scalars(select(Message.conversation_id).where(Message.role == "human", Message.conversation_id.in_(chat_ids))))
    flagged = set(db.scalars(select(Conversation.id).where(Conversation.id.in_(chat_ids), Conversation.needs_human)))
    orders = db.scalars(select(Order).where(Order.created_at >= start, Order.created_at < end)).all()
    live = [o for o in orders if o.status != "cancelled"]
    revenue = sum(o.total for o in live)
    ai_cost, ai_tokens = db.execute(select(func.sum(LlmUsage.cost_usd), func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens))
                                    .where(LlmUsage.created_at >= start, LlmUsage.created_at < end)).one()
    return {"revenue": revenue, "orders": len(live), "cancelled": len(orders) - len(live),
            "ai_cost_usd": round(ai_cost or 0, 4), "ai_tokens": ai_tokens or 0,
            "avg_order": round(revenue / len(live)) if live else 0, "chats": len(chat_ids),
            "conversion_pct": round(100 * len({o.conversation_id for o in live} & chat_ids) / len(chat_ids)) if chat_ids else 0,
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
    # ponytail: aggregates in Python over the period's orders, fine for a small shop; move to SQL GROUP BY past ~100k orders.
    tz = ZoneInfo(BUSINESS.get("timezone", "Asia/Dhaka"))
    first = datetime.now(tz).date() - timedelta(days=days - 1)
    utc = lambda d: datetime.combine(d, time(), tz).astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
    start, end = utc(first), utc(first + timedelta(days=days))
    with Session() as db:
        cur = _period(db, start, end)
        prev = _period(db, utc(first - timedelta(days=days)), start)
    by_day = {first + timedelta(days=i): {"revenue": 0, "orders": 0} for i in range(days)}
    for o in cur["_live"]:
        d = by_day[o.created_at.replace(tzinfo=timezone.utc).astimezone(tz).date()]
        d["revenue"] += o.total
        d["orders"] += 1
    live = cur["_live"]
    return {
        "days": days,
        "current": {k: v for k, v in cur.items() if not k.startswith("_")},
        "previous": {k: v for k, v in prev.items() if not k.startswith("_")},
        "daily": [{"date": d.isoformat(), **v} for d, v in by_day.items()],
        "top_products": _count(((l["name"], l["unit_price"] * l["quantity"], l["quantity"]) for o in live for l in o.items), top=5),
        "payment": _count(((o.payment_method, o.total, 1) for o in live), BUSINESS["payment_methods"]),
        "delivery": _count(((o.delivery_area, o.total, 1) for o in live), BUSINESS["delivery"]),
        "status": [{"status": s, "count": sum(o.status == s for o in cur["_orders"])} for s in ORDER_STATUSES],
    }
