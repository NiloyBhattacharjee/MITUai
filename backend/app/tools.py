"""Agent tools: plain functions over the DB, plus the JSON schemas the LLM sees."""
import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import select, update

from . import hours, notify, options
from .config import MEDIA_DIR, cents, money
from .db import APPROVAL_KINDS, Approval, Booking, Conversation, MenuItem, Message, Order, Setting, now
from .tenancy import cfg, shop_id

log = logging.getLogger(__name__)
MAX_QTY = 20


# The business in scope's settings (its business.yaml). What it does follows from which sections it has:
# fulfilment + payment_methods = it takes orders, reservations = table bookings, appointments = services by appointment.
def sells() -> bool:
    return bool(fulfilment())


def tables() -> bool:
    return "reservations" in cfg()


def appts() -> dict:
    return cfg().get("appointments") or {}


def fulfilment() -> dict:
    return cfg().get("fulfilment", {})


def payments() -> dict:
    return cfg().get("payment_methods", {})


def res() -> dict:
    return cfg()["reservations"]


def prep() -> timedelta:
    return timedelta(minutes=cfg().get("prep_minutes", 15))


def sitting() -> timedelta:
    return timedelta(minutes=res()["sitting_minutes"])


@dataclass
class Ctx:
    db: object  # sqlalchemy Session
    conv: Conversation
    turn_id: int  # id of the customer message this agent turn is answering


# --- menu ---------------------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,.;:!?()/\-–\"'’&]+", text.lower()) if len(t) >= 3]


def _keywords(i: MenuItem) -> set[str]:
    return set(_tokens(" ".join([i.name, i.category, *i.aliases])))


def find_item(db, code) -> MenuItem | None:
    """An item of the business in scope by its code (M01)."""
    return db.scalar(select(MenuItem).where(MenuItem.id == str(code)))


def photo_folder() -> Path:
    return MEDIA_DIR / "menu" / shop_id()


def item_photo(item_id: str) -> Path | None:
    return next((f for f in photo_folder().glob(f"{item_id}.*") if is_photo(f.name)), None)


def is_photo(path: str) -> bool:
    return path.lower().endswith((".jpg", ".jpeg", ".png"))  # the image types WhatsApp accepts


def in_stock(i: MenuItem) -> bool:
    return i.available and i.stock != 0


def item_dict(i: MenuItem) -> dict:
    return {"id": i.id, "name": i.name, "category": i.category, "price": money(i.price), "available": in_stock(i),
            "description": i.description, "options": options.describe(i.options), "dietary": i.dietary,
            "allergens": i.allergens, "has_photo": item_photo(i.id) is not None,
            **({"left": i.stock} if i.stock is not None else {}),
            **({"minutes": i.minutes, "booked_as": "appointment"} if i.minutes else {})}


def search_menu(ctx: Ctx, query: str) -> dict:
    # ponytail: fuzzy scan over the whole menu, fine for hundreds of items; move to DB full-text search past that.
    q = _tokens(query)
    scored = []
    for i in ctx.db.scalars(select(MenuItem)):
        kw = _keywords(i)
        score = sum(any(SequenceMatcher(None, t, k).ratio() >= 0.84 for k in kw) for t in q)
        if score:
            scored.append((score, i))
    scored.sort(key=lambda s: -s[0])
    if not scored:
        cats = sorted({i.category for i in ctx.db.scalars(select(MenuItem))})
        return {"results": [], "note": "Nothing on our menu matches. Don't offer it.", "categories_on_the_menu": cats}
    return {"results": [item_dict(i) for _, i in scored[:6]]}


def get_menu(ctx: Ctx, category: str | None = None) -> dict:
    out = {}
    for i in ctx.db.scalars(select(MenuItem).order_by(MenuItem.id)):
        if not category or i.category == category.lower():
            out.setdefault(i.category, []).append({"id": i.id, "name": i.name, "price": money(i.price), "available": in_stock(i),
                                                  **({"minutes": i.minutes} if i.minutes else {})})
    return {"menu": out} if out else {"error": f"No category {category}", "categories": sorted({i.category for i in ctx.db.scalars(select(MenuItem))})}


def get_item(ctx: Ctx, item_id: str) -> dict:
    i = find_item(ctx.db, item_id)
    return item_dict(i) if i else {"error": f"No menu item with id {item_id}"}


def send_item_photo(ctx: Ctx, item_id: str) -> dict:
    from . import pipeline  # avoid import cycle

    i = find_item(ctx.db, item_id)
    photo = i and item_photo(i.id)
    if not photo:
        return {"error": "No photo for this item. Describe it in words instead."}
    caption = f"{i.name} – {money(i.price)}"
    try:
        pipeline.deliver(ctx.conv.channel, ctx.conv.phone, caption, image=photo)
    except Exception:
        log.exception("sending photo %s failed", photo.name)
        return {"error": "Could not send the photo right now. Describe the item in words instead."}
    ctx.db.add(Message(conversation_id=ctx.conv.id, role="assistant", text=caption, audio_path=photo.relative_to(MEDIA_DIR).as_posix()))
    ctx.db.commit()
    return {"status": "SENT", "instruction": "The photo is already in the chat. Don't describe it at length or send a link."}


# --- orders -------------------------------------------------------------------------------------

def normalize_phone(phone: str) -> str | None:
    p = re.sub(r"[^\d+]", "", phone or "")
    return p if re.fullmatch(r"\+?\d{8,15}", p) else None


def orders_paused(db) -> bool:
    s = db.scalar(select(Setting).where(Setting.key == "orders_paused"))
    return bool(s and s.value)


def line_text(line: dict) -> str:
    text = f"{line['quantity']}× {line['name']}" + (f" ({', '.join(line['options'])})" if line["options"] else "")
    return text + (f" – {line['notes']}" if line.get("notes") else "")


def days_ahead() -> int:
    """How many days ahead orders are taken (orders_days_ahead); 0 = today only."""
    return int(cfg().get("orders_days_ahead", 0))


def _ready_at(when, errors: list) -> datetime | None:
    """"asap", a time today like "13:30", or with orders_days_ahead also "2026-10-14 13:30" or a day "2026-10-14"
    (from opening time) -> local datetime, if it can be ready then."""
    now = hours.local_now()
    earliest = now + prep()
    rule = f"Orders are taken up to {days_ahead()} days ahead." if days_ahead() else "Orders are for today only."
    when = str(when or "asap").strip().lower()
    try:
        if when in ("asap", "now", "soon", ""):
            at = earliest
        elif re.fullmatch(r"\d{4}-\d\d-\d\d", when):
            day = date.fromisoformat(when)
            at = next((max(start, earliest) for start, end in hours.periods(day) if end >= earliest), datetime.combine(day, time()))
        else:
            day, _, clock = when.rpartition(" ")
            at = datetime.combine(date.fromisoformat(day) if day else now.date(), time.fromisoformat(clock))
    except ValueError:
        errors.append('pickup_time must be "asap" or a time today like "13:30"'
                      + (' or a day and time like "2026-10-14 13:30"' if days_ahead() else " (orders are for today only)"))
        return None
    shown = f"{at:%H:%M}" if at.date() == now.date() else hours.label(at)
    if at < earliest:
        errors.append(f"The earliest it can be ready is {earliest:%H:%M}.")
    elif at.date() > now.date() + timedelta(days=days_ahead()):
        errors.append(f"We can't take an order for {at:%A %d %b}. {rule}")
    elif not hours.is_open(at):
        errors.append(f"We can't have it ready at {shown}. On {at:%A}s we're open {hours.spec(at.date())}. {rule}")
    else:
        return at
    return None


def _validate(ctx: Ctx, a: dict) -> tuple[dict | None, list[str]]:
    if orders_paused(ctx.db):
        return None, ["Online orders are paused right now. " + ("Offer to take a table booking, or try again later." if tables() else "Ask them to try again later.")]
    errors, lines = [], []
    for it in a.get("items") or []:
        i = find_item(ctx.db, it.get("item_id", ""))
        if not i:
            errors.append(f"Unknown item {it.get('item_id')}")
            continue
        if i.minutes:
            errors.append(f"{i.name} is a service: book an appointment for it instead")
            continue
        if not in_stock(i):
            errors.append(f"{i.name} is sold out")
            continue
        qty = it.get("quantity")
        if not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
            errors.append(f"{i.name}: quantity must be 1–{MAX_QTY}")
            continue
        if i.stock is not None and qty > i.stock:
            errors.append(f"{i.name}: only {i.stock} left")
            continue
        picked, extra, problems = options.choose(i.options, it.get("options") or [])
        errors += [f"{i.name}: {p}" for p in problems]
        lines.append({"item_id": i.id, "name": i.name, "quantity": qty, "options": picked,
                      "notes": str(it.get("notes") or "").strip()[:200], "unit_price": i.price + extra})
    if not lines and not errors:
        errors.append("No items")
    phone = normalize_phone(a.get("phone", ""))
    if not phone:
        errors.append("phone must be a valid phone number")
    if not str(a.get("customer_name", "")).strip():
        errors.append("customer_name is missing")
    way = a.get("fulfilment")
    if way not in fulfilment():
        errors.append(f"fulfilment must be one of {list(fulfilment())}")
    elif fulfilment()[way].get("needs_address") and not str(a.get("address", "")).strip():
        errors.append("address is needed for delivery")
    pay = a.get("payment_method") or (next(iter(payments())) if len(payments()) == 1 else None)
    if pay not in payments():
        errors.append(f"payment_method must be one of {list(payments())}")
    at = _ready_at(a.get("pickup_time"), errors)
    if errors:
        return None, errors
    subtotal = sum(line["unit_price"] * line["quantity"] for line in lines)
    fee = cents(fulfilment()[way].get("fee", 0))
    return {"customer_name": a["customer_name"].strip(), "phone": phone, "fulfilment": way,
            "address": str(a.get("address") or "").strip() if fulfilment()[way].get("needs_address") else None,
            "ready_at": at.isoformat(), "payment_method": pay, "notes": str(a.get("notes") or "").strip()[:500] or None,
            "items": lines, "subtotal": subtotal, "fee": fee, "total": subtotal + fee}, []


def _summary(q: dict) -> dict:
    """The quote as the customer should see it: amounts formatted, the time spelled out."""
    return {"items": [f"{line_text(line)}: {money(line['unit_price'] * line['quantity'])}" for line in q["items"]],
            "fulfilment": fulfilment()[q["fulfilment"]]["label"], "address": q["address"], "ready_at": hours.label(datetime.fromisoformat(q["ready_at"])),
            "name": q["customer_name"], "phone": q["phone"], "notes": q["notes"], "subtotal": money(q["subtotal"]),
            "fee": money(q["fee"]), "total": money(q["total"]), "payment": payments()[q["payment_method"]]["label"]}


def create_order(ctx: Ctx, confirmed: bool = False, **a) -> dict:
    if not confirmed:
        quote, errors = _validate(ctx, a)
        if errors:
            return {"error": "Cannot quote yet", "problems": errors}
        ctx.conv.pending_order = {"args": a, "total": quote["total"], "quoted_turn": ctx.turn_id, "quoted_at": now().isoformat()}
        ctx.db.commit()
        return {"status": "QUOTE_ONLY_NOT_PLACED",
                "instruction": "Show this summary with the total and ask the customer to confirm. Do not say the order is placed.",
                "summary": _summary(quote)}

    pending = ctx.conv.pending_order
    # The guard: a quote must have been shown in an EARLIER turn, so the customer has replied to it.
    if not pending or pending["quoted_turn"] >= ctx.turn_id:
        return {"error": "Not placed. First call create_order with confirmed=false, show the summary, "
                         "and wait for the customer's yes in their next message."}
    quote, errors = _validate(ctx, pending["args"])  # again: something may have sold out or the kitchen closed since
    if errors:
        return {"error": "Not placed: things changed since the quote.", "problems": errors}
    if quote["total"] != pending["total"]:
        ctx.conv.pending_order = {**pending, "total": quote["total"], "quoted_turn": ctx.turn_id, "quoted_at": now().isoformat(), "reminded": False}
        ctx.db.commit()
        return {"status": "QUOTE_CHANGED_NOT_PLACED", "instruction": "Prices changed. Show the new summary and ask again.",
                "summary": _summary(quote)}
    if short := take_stock(ctx.db, quote["items"]):
        ctx.db.rollback()
        return {"error": "Not placed: things changed since the quote.", "problems": [f"{n}: not enough left" for n in short]}
    order = Order(conversation_id=ctx.conv.id, **{**quote, "ready_at": hours.to_utc(datetime.fromisoformat(quote["ready_at"]))})
    ctx.db.add(order)
    ctx.conv.pending_order = None
    ctx.db.commit()
    return {"status": "PLACED", "order_id": order.id, "total": money(order.total), "ready_at": hours.label(hours.to_local(order.ready_at)),
            "payment_note": payments()[order.payment_method]["note"], "fulfilment_note": fulfilment()[order.fulfilment].get("note")}


def take_stock(db, lines: list[dict], back: bool = False) -> list[str]:
    """Count an order's items out of stock, or back in (back=True, when it's cancelled). Items without a stock count
    are skipped. Returns the names that ran short; the caller rolls back. Each update only applies if enough are left,
    so two customers can't both buy the last one."""
    short = []
    for line in lines:
        n = -line["quantity"] if back else line["quantity"]
        done = db.execute(update(MenuItem).where(MenuItem.id == line["item_id"], MenuItem.stock.is_not(None), MenuItem.stock >= n)
                          .values(stock=MenuItem.stock - n)).rowcount
        if not done and (i := find_item(db, line["item_id"])) and i.stock is not None:
            short.append(i.name)
    return short


def order_dict(o: Order) -> dict:
    return {"order_id": o.id, "status": o.status, "fulfilment": fulfilment().get(o.fulfilment, {}).get("label", o.fulfilment),
            "ready_at": hours.label(hours.to_local(o.ready_at)), "total": money(o.total), "items": [line_text(line) for line in o.items]}


def _own(ctx: Ctx, model, key):
    """A row from this chat only: customers can't look up or change other people's orders and bookings."""
    row = ctx.db.get(model, int(key))
    return row if row and row.conversation_id == ctx.conv.id else None


def get_order_status(ctx: Ctx, order_id: int | None = None) -> dict:
    if order_id:
        o = _own(ctx, Order, order_id)
        return order_dict(o) if o else {"error": f"No order #{order_id} was placed in this chat."}
    orders = ctx.db.scalars(select(Order).where(Order.conversation_id == ctx.conv.id).order_by(Order.id.desc()).limit(3)).all()
    return {"orders": [order_dict(o) for o in orders]} if orders else {"error": "No orders in this chat."}


def cancel_order(ctx: Ctx, order_id: int) -> dict:
    o = _own(ctx, Order, order_id)
    if not o:
        return {"error": f"No order #{order_id} was placed in this chat."}
    if o.status != "new":
        return {"error": f"Order #{o.id} is {o.status}: we've already started on it, so it can't be cancelled here.",
                "instruction": "Offer to connect a person."}
    o.status = "cancelled"
    take_stock(ctx.db, o.items, back=True)
    ctx.db.commit()
    return {"status": "CANCELLED", "order_id": o.id}


def saved_details(ctx: Ctx) -> dict:
    # Only this chat's own history: looking up by a typed phone number would hand out strangers' details.
    o = ctx.db.scalar(select(Order).where(Order.conversation_id == ctx.conv.id).order_by(Order.id.desc()).limit(1))
    b = ctx.db.scalar(select(Booking).where(Booking.conversation_id == ctx.conv.id).order_by(Booking.id.desc()).limit(1))
    latest = max((x for x in (o, b) if x), key=lambda x: x.created_at, default=None)
    if not latest:
        return {"note": "No earlier order or booking in this chat. Ask for the details."}
    return {"customer_name": latest.customer_name, "phone": latest.phone, "address": o.address if o else None,
            "instruction": "Ask whether to use these again instead of asking for each one."}


# --- bookings -----------------------------------------------------------------------------------

def _bookable(at: datetime) -> bool:
    last = timedelta(minutes=res().get("last_booking_before_close", 60))
    return any(start <= at <= end - last for start, end in hours.periods(at.date()))


def _seats_taken(db, at: datetime) -> int:
    # ponytail: counts every booking within a sitting of this one as overlapping it, which over-counts a little
    # (two that don't overlap each other both block this one); a per-table plan would be exact.
    lo, hi = hours.to_utc(at - sitting()), hours.to_utc(at + sitting())
    return sum(b.party_size for b in db.scalars(select(Booking).where(Booking.status == "booked", Booking.service_id.is_(None),
                                                                      Booking.at > lo, Booking.at < hi)))


def _fits(db, at: datetime, party: int) -> bool:
    return _bookable(at) and at >= hours.local_now() and _seats_taken(db, at) + party <= res()["capacity"]


def _alternatives(db, day: date, party: int, near: datetime) -> list[str]:
    slots = [start + timedelta(minutes=15 * k) for start, end in hours.periods(day) for k in range(int((end - start).total_seconds() // 900) + 1)]
    free = sorted((s for s in slots if _fits(db, s, party)), key=lambda s: abs(s - near))
    return [f"{s:%H:%M}" for s in sorted(free[:3])]


def _booking_time(date_s: str, time_s: str, party, errors: list, limit_party=True) -> datetime | None:
    try:
        at = datetime.combine(date.fromisoformat(str(date_s)), time.fromisoformat(str(time_s)))
    except ValueError:
        errors.append("date must be YYYY-MM-DD and time HH:MM (24-hour)")
        return None
    now = hours.local_now()
    if not isinstance(party, int) or party < 1:
        errors.append("party_size must be a whole number of people")
    elif limit_party and party > res()["max_party"]:
        errors.append(f"We book groups of up to {res()['max_party']} here; bigger groups are arranged by the team.")
    if at < now:
        errors.append(f"That time has passed (it's now {hours.label(now)}).")
    elif at.date() > now.date() + timedelta(days=res()["days_ahead"]):
        errors.append(f"We take bookings up to {res()['days_ahead']} days ahead.")
    elif not _bookable(at):
        errors.append(f"We can't book {hours.label(at)}. On {at:%A}s we're open {hours.spec(at.date())}, and the last booking is "
                      f"{res().get('last_booking_before_close', 60)} minutes before closing.")
    return None if errors else at


def check_availability(ctx: Ctx, date: str, time: str, party_size: int) -> dict:
    errors = []
    at = _booking_time(date, time, party_size, errors)
    if errors:
        return {"available": False, "problems": errors, **({"instruction": "Get the name and phone, then call request_approval "
                                                                            "with kind large_booking and the booking details."}
                                                           if isinstance(party_size, int) and party_size > res()["max_party"] else {})}
    if _fits(ctx.db, at, party_size):
        return {"available": True, "when": hours.label(at), "party_size": party_size}
    return {"available": False, "when": hours.label(at), "problems": ["Fully booked at that time."],
            "other_times_that_day": _alternatives(ctx.db, at.date(), party_size, at)}


def book_table(ctx: Ctx, customer_name: str, phone: str, party_size: int, date: str, time: str, notes: str = "") -> dict:
    errors = []
    at = _booking_time(date, time, party_size, errors)
    if not (p := normalize_phone(phone)):
        errors.append("phone must be a valid phone number")
    if not str(customer_name).strip():
        errors.append("customer_name is missing")
    if errors:
        return {"error": "Not booked", "problems": errors}
    utc = hours.to_utc(at)
    if same := ctx.db.scalar(select(Booking).where(Booking.conversation_id == ctx.conv.id, Booking.at == utc, Booking.status == "booked")):
        return {"status": "ALREADY_BOOKED", "booking_id": same.id, "when": hours.label(at), "party_size": same.party_size}
    if not _fits(ctx.db, at, party_size):
        return {"error": "Fully booked at that time.", "other_times_that_day": _alternatives(ctx.db, at.date(), party_size, at)}
    b = Booking(conversation_id=ctx.conv.id, customer_name=customer_name.strip(), phone=p, party_size=party_size, at=utc,
                notes=str(notes or "").strip()[:500] or None)
    ctx.db.add(b)
    ctx.db.commit()
    return {"status": "BOOKED", "booking_id": b.id, "when": hours.label(at), "party_size": party_size}


def booking_dict(b: Booking) -> dict:
    what = {"service": b.service, "with": b.provider, "minutes": b.minutes} if b.service_id else {"party_size": b.party_size}
    return {"booking_id": b.id, "status": b.status, "when": hours.label(hours.to_local(b.at)), **what, "name": b.customer_name,
            "notes": b.notes}


def my_bookings(ctx: Ctx) -> dict:
    rows = ctx.db.scalars(select(Booking).where(Booking.conversation_id == ctx.conv.id).order_by(Booking.at.desc()).limit(5)).all()
    return {"bookings": [booking_dict(b) for b in rows]} if rows else {"error": "No bookings made in this chat."}


def cancel_booking(ctx: Ctx, booking_id: int) -> dict:
    b = _own(ctx, Booking, booking_id)
    if not b:
        return {"error": f"No booking #{booking_id} was made in this chat."}
    if b.status != "booked" or hours.to_local(b.at) < hours.local_now():
        return {"error": f"Booking #{b.id} is {b.status} and can't be cancelled here."}
    b.status = "cancelled"
    ctx.db.commit()
    return {"status": "CANCELLED", "booking_id": b.id}


# --- appointments -------------------------------------------------------------------------------

_booking_lock = threading.Lock()
# ponytail: one server process (like resume_unfinished), so checking that a time is free and booking it can't
# interleave; with several workers this needs a database lock.


def providers(service_id: str) -> list[str]:
    """Who does a service: appointments.providers is {name: "all" or [service ids]}."""
    return [p for p, does in appts().get("providers", {}).items() if does == "all" or service_id in does]


def _service(db, service_id, errors: list) -> MenuItem | None:
    i = find_item(db, service_id)
    if not i or not i.minutes:
        errors.append(f"No service {service_id}. Find it with search_menu (services have minutes).")
    elif not i.available:
        errors.append(f"{i.name} isn't on offer right now.")
    else:
        return i
    return None


def _appointment_at(date_s, time_s, errors: list) -> datetime | None:
    """A local start time from "YYYY-MM-DD" and "HH:MM" (time_s None: the start of that day), within days_ahead."""
    try:
        at = datetime.combine(date.fromisoformat(str(date_s)), time.fromisoformat(str(time_s)) if time_s else time())
    except ValueError:
        errors.append("date must be YYYY-MM-DD and time HH:MM (24-hour)")
        return None
    today, ahead = hours.local_now().date(), appts().get("days_ahead", 30)
    if at.date() < today:
        errors.append("That day has passed.")
    elif at.date() > today + timedelta(days=ahead):
        errors.append(f"We book up to {ahead} days ahead.")
    elif not hours.periods(at.date()):
        errors.append(f"We're closed on {at:%A}s. Our hours: {hours.weekly()}")
    else:
        return at
    return None


def _busy(db, provider: str, start: datetime, end: datetime) -> bool:
    """Whether the provider has an appointment overlapping start-end (local)."""
    rows = db.scalars(select(Booking).where(Booking.provider == provider, Booking.status.in_(("booked", "arrived")), Booking.at < hours.to_utc(end),
                                            Booking.at > hours.to_utc(start - timedelta(days=1))))
    return any(hours.to_local(b.at) + timedelta(minutes=b.minutes or 0) > start for b in rows)


def _free(db, service: MenuItem, start: datetime, provider: str | None = None) -> list[str]:
    """Who can do the service starting then: inside opening hours, not in the past, not already booked."""
    end = start + timedelta(minutes=service.minutes)
    if start < hours.local_now() or not any(s <= start and end <= e for s, e in hours.periods(start.date())):
        return []
    return [p for p in providers(service.id) if provider in (None, p) and not _busy(db, p, start, end)]


def _slots(db, service: MenuItem, day: date, provider: str | None = None) -> dict[str, list[str]]:
    """{"14:30": [who is free]} for every start time that day, every slot_minutes."""
    step, out = timedelta(minutes=appts().get("slot_minutes", 15)), {}
    for start, end in hours.periods(day):
        t = start
        while t + timedelta(minutes=service.minutes) <= end:
            if free := _free(db, service, t, provider):
                out[f"{t:%H:%M}"] = free
            t += step
    return out


def _check_provider(service: MenuItem | None, provider: str | None, errors: list):
    if service and provider and provider not in providers(service.id):
        errors.append(f"{provider} doesn't do {service.name}. Who does: {', '.join(providers(service.id))}")


def appointment_times(ctx: Ctx, service_id: str, date: str, provider: str | None = None) -> dict:
    errors = []
    service, day = _service(ctx.db, service_id, errors), _appointment_at(date, None, errors)
    _check_provider(service, provider, errors)
    if errors:
        return {"available": False, "problems": errors}
    free = _slots(ctx.db, service, day.date(), provider)
    if not free:
        return {"available": False, "problems": [f"Nothing free for {service.name} on {day:%A %d %b}" + (f" with {provider}" if provider else "")],
                "instruction": "Offer another day" + (" or someone else." if provider else ".")}
    return {"service": service.name, "minutes": service.minutes, "price": money(service.price), "day": f"{day:%A %d %b}", "free_times": free}


def book_appointment(ctx: Ctx, customer_name: str, phone: str, service_id: str, date: str, time: str, provider: str | None = None,
                     notes: str = "") -> dict:
    errors = []
    service, at = _service(ctx.db, service_id, errors), _appointment_at(date, time, errors)
    _check_provider(service, provider, errors)
    if not (p := normalize_phone(phone)):
        errors.append("phone must be a valid phone number")
    if not str(customer_name).strip():
        errors.append("customer_name is missing")
    if errors:
        return {"error": "Not booked", "problems": errors}
    utc = hours.to_utc(at)
    if same := ctx.db.scalar(select(Booking).where(Booking.conversation_id == ctx.conv.id, Booking.service_id == service.id,
                                                   Booking.at == utc, Booking.status == "booked")):
        return {"status": "ALREADY_BOOKED", "booking_id": same.id, "service": same.service, "with": same.provider, "when": hours.label(at)}
    with _booking_lock:
        free = _free(ctx.db, service, at, provider)
        if not free:
            return {"error": "That time isn't free.", "other_times_that_day": list(_slots(ctx.db, service, at.date(), provider))[:6]}
        b = Booking(conversation_id=ctx.conv.id, customer_name=customer_name.strip(), phone=p, party_size=1, at=utc,
                    service_id=service.id, service=service.name, provider=free[0], minutes=service.minutes,
                    notes=str(notes or "").strip()[:500] or None)
        ctx.db.add(b)
        ctx.db.commit()
    return {"status": "BOOKED", "booking_id": b.id, "service": b.service, "with": b.provider, "when": hours.label(at),
            "minutes": b.minutes, "price": money(service.price)}


def request_approval(ctx: Ctx, kind: str, summary: str, order_id: int | None = None, booking: dict | None = None) -> dict:
    if kind not in APPROVAL_KINDS or (kind == "large_booking" and not tables()):
        return {"error": f"kind must be one of {[k for k in APPROVAL_KINDS if k != 'large_booking' or tables()]}"}
    if not str(summary or "").strip():
        return {"error": "summary is missing: say in one line what the customer is asking for"}
    if order_id and not _own(ctx, Order, order_id):
        return {"error": f"No order #{order_id} was placed in this chat."}
    details = None
    if kind == "large_booking":
        b, errors = booking or {}, []
        at = _booking_time(b.get("date"), b.get("time"), b.get("party_size"), errors, limit_party=False)
        if not (phone := normalize_phone(b.get("phone", ""))):
            errors.append("booking.phone must be a valid phone number")
        if not str(b.get("customer_name", "")).strip():
            errors.append("booking.customer_name is missing")
        if errors:
            return {"error": "Not sent to the team yet", "problems": errors}
        details = {"customer_name": b["customer_name"].strip(), "phone": phone, "party_size": b["party_size"], "at": at.isoformat(),
                   "notes": str(b.get("notes") or "").strip()[:500] or None}
    waiting = ctx.db.scalar(select(Approval).where(Approval.conversation_id == ctx.conv.id, Approval.kind == kind, Approval.status == "pending"))
    if waiting:
        return {"status": "ALREADY_WAITING", "approval_id": waiting.id, "instruction": "The team already has this request; they'll reply here."}
    a = Approval(conversation_id=ctx.conv.id, kind=kind, summary=str(summary).strip()[:500], order_id=int(order_id) if order_id else None,
                 details=details)
    ctx.db.add(a)
    ctx.db.commit()
    notify.email_owner(f"Approval needed: {kind.replace('_', ' ')}",
                       f"{a.summary}\n\nApprove or decline it on the dashboard's Approvals page. The customer is told your answer.")
    return {"status": "WAITING_FOR_STAFF", "approval_id": a.id,
            "instruction": "Tell the customer the team will check and reply here soon. Don't promise the outcome."}


# --- the business -------------------------------------------------------------------------------

def topics() -> list[str]:
    """What get_business_info can tell, given what this business does."""
    return ["hours", "location", *(["ordering", "payment"] if sells() else []), *(["bookings"] if tables() else []),
            *(["appointments"] if appts() else []), *(["allergens"] if cfg().get("allergen_note") else []), "about"]


def get_business_info(ctx: Ctx, topic: str) -> dict:
    if topic not in topics():
        return {"error": f"topic must be one of {topics()}"}
    b, now = cfg(), hours.local_now()
    if topic == "hours":
        return {topic: {"weekly": hours.weekly(), "open_now": hours.is_open(now), "now": hours.label(now)}}
    if topic == "ordering":
        return {topic: {"ways": {k: {"label": v["label"], "fee": money(cents(v.get("fee", 0))), "note": v.get("note")} for k, v in fulfilment().items()},
                        "ready_in_minutes": int(prep().total_seconds() // 60), "orders_paused": orders_paused(ctx.db),
                        "orders_for": f"today and up to {days_ahead()} days ahead" if days_ahead() else "today only"}}
    if topic == "payment":
        return {topic: payments()}
    if topic == "bookings":
        return {topic: {"max_party": res()["max_party"], "days_ahead": res()["days_ahead"], "sitting_minutes": res()["sitting_minutes"]}}
    if topic == "appointments":
        does = {p: "everything" if s == "all" else s for p, s in appts().get("providers", {}).items()}
        return {topic: {"days_ahead": appts().get("days_ahead", 30), "who_does_what": does, "policy": appts().get("policy")}}
    return {topic: b["allergen_note" if topic == "allergens" else topic]}


def escalate_to_human(ctx: Ctx, reason: str) -> dict:
    notify.flag_for_human(ctx.db, ctx.conv, reason, stop_bot=True)
    return {"status": "escalated", "instruction": "Tell the customer briefly that a team member will reply here soon."}


FUNCTIONS = {f.__name__: f for f in [search_menu, get_menu, get_item, send_item_photo, saved_details, create_order, get_order_status,
                                     cancel_order, check_availability, book_table, appointment_times, book_appointment, my_bookings,
                                     cancel_booking, request_approval, get_business_info, escalate_to_human]}


def enabled() -> set[str]:
    """The tools this business uses: no ordering tools for a salon, no table bookings for a shop."""
    off = set()
    if not sells():
        off |= {"create_order", "get_order_status", "cancel_order"}
    if not tables():
        off |= {"check_availability", "book_table"}
    if not appts():
        off |= {"appointment_times", "book_appointment"}
    if not (tables() or appts()):
        off |= {"my_bookings", "cancel_booking"}
    return set(FUNCTIONS) - off

_id = {"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"]}
_line = {"type": "object", "properties": {
    "item_id": {"type": "string"}, "quantity": {"type": "integer"},
    "options": {"type": "array", "items": {"type": "string"}, "description": "Choice names, e.g. [\"Large\", \"Oat\"]"},
    "notes": {"type": "string", "description": "What the customer asked for on this item, e.g. extra hot, gift wrap"}},
    "required": ["item_id", "quantity"]}
_when = {"date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string", "description": "HH:MM, 24-hour"},
         "party_size": {"type": "integer"}}

def specs() -> list[dict]:
    """What the LLM sees, built per business: only the tools it uses, and its pickup/delivery and payment choices."""
    on = enabled()
    return [s for s in _all_specs() if s["name"] in on]


def _all_specs() -> list[dict]:
    when = ('"asap", a time today like "13:30", or a day and time up to '
            f'{days_ahead()} days ahead like "2026-10-14 13:30"') if days_ahead() else '"asap" or a time today like "13:30"'
    return [
        {"name": "search_menu",
         "description": "Search what we sell or offer (the menu) by name or kind, e.g. 'flat white', 'vegan breakfast', 'blue kurti', "
                        "'haircut'. Returns price, options with their extra cost, dietary tags, allergens, whether it's available, "
                        "how many are left (if counted), and minutes for services booked as appointments.",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
        {"name": "get_menu", "description": "Everything we sell or offer (or one category) with prices, for 'what do you have?' questions.",
         "input_schema": {"type": "object", "properties": {"category": {"type": "string"}}}},
        {"name": "get_item", "description": "Full details of one item.", "input_schema": _id},
        {"name": "send_item_photo", "description": "Send the customer a photo of an item (only items with has_photo=true).", "input_schema": _id},
        {"name": "saved_details", "description": "Name, phone and address from this customer's last order or booking in this chat, if any.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "create_order",
         "description": f"Pickup or delivery orders, {'up to ' + str(days_ahead()) + ' days ahead' if days_ahead() else 'for today only'}. Two steps. confirmed=false with all details: checks everything and returns "
                        "the official summary and total (nothing is placed). confirmed=true (no other fields): places the last quoted order; "
                        "only call it after the customer said yes to that summary in a later message.",
         "input_schema": {"type": "object", "properties": {
             "confirmed": {"type": "boolean"},
             "customer_name": {"type": "string"}, "phone": {"type": "string"},
             "fulfilment": {"type": "string", "enum": list(fulfilment()) if sells() else []},
             "address": {"type": "string", "description": "Only for delivery"},
             "pickup_time": {"type": "string", "description": when},
             "payment_method": {"type": "string", "enum": list(payments()) if sells() else []},
             "notes": {"type": "string"},
             "items": {"type": "array", "items": _line}}, "required": ["confirmed"]}},
        {"name": "get_order_status", "description": "Orders placed in this chat: one by number, or the latest ones.",
         "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}}}},
        {"name": "cancel_order",
         "description": "Cancel an order from this chat that we haven't started on. Only after the customer clearly asked to cancel it.",
         "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}}, "required": ["order_id"]}},
        {"name": "check_availability", "description": "Whether a table is free for a party at a date and time; suggests other times if not.",
         "input_schema": {"type": "object", "properties": _when, "required": ["date", "time", "party_size"]}},
        {"name": "book_table",
         "description": "Book a table. Only after you've repeated the date, time, party size and name to the customer and they agreed.",
         "input_schema": {"type": "object", "properties": {
             "customer_name": {"type": "string"}, "phone": {"type": "string"}, **_when,
             "notes": {"type": "string", "description": "e.g. high chair, birthday, outside table"}},
             "required": ["customer_name", "phone", "date", "time", "party_size"]}},
        {"name": "appointment_times",
         "description": "Free start times for a service on a day, and who is free at each. Pass provider only if the customer asked for someone.",
         "input_schema": {"type": "object", "properties": {
             "service_id": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"}, "provider": {"type": "string"}},
             "required": ["service_id", "date"]}},
        {"name": "book_appointment",
         "description": "Book a service. Only after you've repeated the service, weekday, date, time and who it's with, and the customer agreed.",
         "input_schema": {"type": "object", "properties": {
             "customer_name": {"type": "string"}, "phone": {"type": "string"}, "service_id": {"type": "string"},
             "date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string", "description": "HH:MM, 24-hour"},
             "provider": {"type": "string", "description": "Only if the customer asked for someone"},
             "notes": {"type": "string", "description": "e.g. first visit, hair length, what they'd like done"}},
             "required": ["customer_name", "phone", "service_id", "date", "time"]}},
        {"name": "my_bookings", "description": "Bookings and appointments made in this chat.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "cancel_booking", "description": "Cancel a booking or appointment made in this chat. Only after the customer clearly asked to.",
         "input_schema": {"type": "object", "properties": {"booking_id": {"type": "integer"}}, "required": ["booking_id"]}},
        {"name": "request_approval",
         "description": "Ask the team to decide something you can't: a refund or remake for an order (refund), a discount or freebie "
                        "(discount), a booking for a group bigger than we book (large_booking, with the booking details), or another "
                        "exception (special_request). The team answers in the dashboard and the customer is told here automatically.",
         "input_schema": {"type": "object", "properties": {
             "kind": {"type": "string", "enum": APPROVAL_KINDS},
             "summary": {"type": "string", "description": "One line for the team, e.g. 'Refund for order #12: flat white was cold'"},
             "order_id": {"type": "integer"},
             "booking": {"type": "object", "description": "Only for large_booking", "properties": {
                 "customer_name": {"type": "string"}, "phone": {"type": "string"}, **_when, "notes": {"type": "string"}}}},
             "required": ["kind", "summary"]}},
        {"name": "get_business_info",
         "description": "Facts about us: opening hours (and whether we're open now), location and parking, ordering (pickup/delivery, "
                        "fees, times, whether orders are paused), payment, booking or appointment rules, allergen policy, about.",
         "input_schema": {"type": "object", "properties": {
             "topic": {"type": "string", "enum": topics()}},
             "required": ["topic"]}},
        {"name": "escalate_to_human",
         "description": "Hand the chat to a person. Use when the customer asks for a person, is upset or angry, has a serious "
                        "allergy question, wants a large or custom order or an event, or anything you can't answer from the tools.",
         "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
    ]


def run_tool(ctx: Ctx, name: str, args: dict) -> dict:
    if name not in enabled():
        return {"error": f"Unknown tool {name}"}
    try:
        return FUNCTIONS[name](ctx, **args)
    except (TypeError, ValueError) as e:  # bad/missing arguments from the model
        return {"error": str(e)}
