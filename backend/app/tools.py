"""Agent tools: plain functions over the DB, plus the JSON schemas the LLM sees."""
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import select, update

from . import notify
from .config import BUSINESS, MEDIA_DIR
from .db import Conversation, Message, Order, Product, Variant

log = logging.getLogger(__name__)
DELIVERY = BUSINESS["delivery"]
PAYMENTS = BUSINESS["payment_methods"]
MAX_QTY = 10
LOW_STOCK = 3  # email the owner when an order takes a size/colour down to this many


@dataclass
class Ctx:
    db: object  # sqlalchemy Session
    conv: Conversation
    turn_id: int  # id of the customer message this agent turn is answering


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,.;:!?()/\-–।\"'’]+", text.lower()) if len(t) >= 3]


def _keywords(p: Product) -> set[str]:
    return set(_tokens(" ".join([p.name_en, p.name_bn, p.category, *p.aliases])))


def product_dict(p: Product) -> dict:
    stock = {f"{s} / {c}": p.stock_of(s, c) for s in p.sizes for c in p.colours}
    return {"id": p.id, "name_en": p.name_en, "name_bn": p.name_bn, "price_bdt": p.price, "sizes": p.sizes,
            "colours": p.colours, "stock_by_size_colour": stock, "total_stock": sum(stock.values()),
            "has_photo": product_photo(p.id) is not None}


def search_products(ctx: Ctx, query: str) -> dict:
    # ponytail: fuzzy scan over all products, fine for hundreds; move to DB full-text search past that.
    q = _tokens(query)
    scored = []
    for p in ctx.db.scalars(select(Product)):
        kw = _keywords(p)
        score = sum(any(SequenceMatcher(None, t, k).ratio() >= 0.84 for k in kw) for t in q)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda s: -s[0])
    if not scored:
        cats = sorted({p.category for p in ctx.db.scalars(select(Product))})
        return {"results": [], "note": "No matching product. We do not sell it.", "categories_we_sell": cats}
    return {"results": [product_dict(p) for _, p in scored[:8]]}


def get_product(ctx: Ctx, product_id: str) -> dict:
    p = ctx.db.get(Product, product_id)
    return product_dict(p) if p else {"error": f"No product with id {product_id}"}


def normalize_phone(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("880"):
        digits = "0" + digits[3:]
    return digits if re.fullmatch(r"01[3-9]\d{8}", digits) else None


def quote_total(lines: list[dict], delivery_area: str) -> tuple[int, int, int]:
    subtotal = sum(l["unit_price"] * l["quantity"] for l in lines)
    charge = DELIVERY[delivery_area]["charge"]
    return subtotal, charge, subtotal + charge


def _pick(value: str, options: list[str]) -> str | None:
    return next((o for o in options if o.lower() == str(value).strip().lower()), None)


def _validate(ctx: Ctx, a: dict) -> tuple[dict | None, list[str]]:
    errors, lines, wanted = [], [], {}
    for item in a.get("items") or []:
        p = ctx.db.get(Product, item.get("product_id", ""))
        if not p:
            errors.append(f"Unknown product {item.get('product_id')}")
            continue
        size, colour = _pick(item.get("size", ""), p.sizes), _pick(item.get("colour", ""), p.colours)
        qty = item.get("quantity")
        if not size:
            errors.append(f"{p.name_en}: size must be one of {p.sizes}")
        if not colour:
            errors.append(f"{p.name_en}: colour must be one of {p.colours}")
        if not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
            errors.append(f"{p.name_en}: quantity must be 1–{MAX_QTY}")
            continue
        if not (size and colour):
            continue
        key = (p.id, size, colour)
        wanted[key] = wanted.get(key, 0) + qty
        if wanted[key] > (left := p.stock_of(size, colour)):
            errors.append(f"{p.name_en} ({size}, {colour}): only {left} in stock")
        lines.append({"product_id": p.id, "name": p.name_en, "size": size, "colour": colour,
                      "quantity": qty, "unit_price": p.price})
    if not lines and not errors:
        errors.append("No items")
    phone = normalize_phone(a.get("phone", ""))
    if not phone:
        errors.append("phone must be a valid Bangladeshi mobile number like 01712345678")
    for field in ("customer_name", "address"):
        if not str(a.get(field, "")).strip():
            errors.append(f"{field} is missing")
    if a.get("delivery_area") not in DELIVERY:
        errors.append(f"delivery_area must be one of {list(DELIVERY)}")
    if a.get("payment_method") not in PAYMENTS:
        errors.append(f"payment_method must be one of {list(PAYMENTS)}")
    if errors:
        return None, errors
    subtotal, charge, total = quote_total(lines, a["delivery_area"])
    return {"customer_name": a["customer_name"].strip(), "phone": phone, "address": a["address"].strip(),
            "delivery_area": a["delivery_area"], "payment_method": a["payment_method"], "items": lines,
            "subtotal": subtotal, "delivery_charge": charge, "total": total}, []


def _variant(line: dict):
    return (Variant.product_id == line["product_id"]) & (Variant.size == line["size"]) & (Variant.colour == line["colour"])


def reserve_stock(db, items: list[dict]) -> str | None:
    """Take order lines out of stock. If one is short, roll back and return its name."""
    low = []
    for line in items:
        res = db.execute(update(Variant).where(_variant(line), Variant.stock >= line["quantity"])
                         .values(stock=Variant.stock - line["quantity"]))
        if res.rowcount == 0:
            db.rollback()
            return f"{line['name']} ({line['size']}, {line['colour']})"
        left = db.scalar(select(Variant.stock).where(_variant(line)))
        if left <= LOW_STOCK < left + line["quantity"]:  # only when this order crosses the line, not on every sale after
            low.append(f"{line['name']} ({line['size']}, {line['colour']}): {left} left")
    if low:
        notify.email_owner("Low stock: " + ", ".join(l.split(" (")[0] for l in low),
                           "These are running low:\n\n" + "\n".join(low) + "\n\nUpdate stock on the Products page.")
    return None


def release_stock(db, items: list[dict]):
    for line in items:  # a size/colour removed since the order was placed just has nothing to return to
        db.execute(update(Variant).where(_variant(line)).values(stock=Variant.stock + line["quantity"]))


def create_order(ctx: Ctx, confirmed: bool = False, **a) -> dict:
    if not confirmed:
        quote, errors = _validate(ctx, a)
        if errors:
            return {"error": "Cannot quote yet", "problems": errors}
        ctx.conv.pending_order = {**quote, "quoted_turn": ctx.turn_id}
        ctx.db.commit()
        return {"status": "QUOTE_ONLY_NOT_PLACED",
                "instruction": "Show this summary with the total and ask the customer to confirm. Do not say the order is placed.",
                "summary": quote}

    pending = ctx.conv.pending_order
    # The guard: a quote must have been shown in an EARLIER turn, so the customer has replied to it.
    if not pending or pending["quoted_turn"] >= ctx.turn_id:
        return {"error": "Not placed. First call create_order with confirmed=false, show the summary, "
                         "and wait for the customer's yes in their next message."}
    if short := reserve_stock(ctx.db, pending["items"]):
        return {"error": f"{short} just went out of stock. Not placed."}
    data = {k: v for k, v in pending.items() if k != "quoted_turn"}
    order = Order(conversation_id=ctx.conv.id, **data)
    ctx.db.add(order)
    ctx.conv.pending_order = None
    ctx.db.commit()
    return {"status": "PLACED", "order_id": order.id, "total": order.total,
            "payment_note": PAYMENTS[order.payment_method]["note"]}


def order_dict(o: Order) -> dict:
    return {"order_id": o.id, "status": o.status, "created": o.created_at.date().isoformat(), "total": o.total,
            "items": [f"{l['quantity']}x {l['name']} ({l['size']}, {l['colour']})" for l in o.items],
            "payment_method": o.payment_method, "paid": bool(o.paid), "transaction_id": o.payment_ref}


def record_payment(ctx: Ctx, order_id: int, transaction_id: str) -> dict:
    o = ctx.db.get(Order, int(order_id))
    if not o or o.conversation_id != ctx.conv.id:
        return {"error": f"No order #{order_id} was placed in this chat."}
    if o.payment_method not in ("bkash", "nagad"):
        return {"error": "This order is cash on delivery, so there is no payment to record."}
    ref = re.sub(r"\s", "", transaction_id).upper()
    if not re.fullmatch(r"[A-Z0-9]{8,20}", ref):
        return {"error": "That doesn't look like a transaction ID. Ask the customer to copy the TrxID from their payment SMS."}
    o.payment_ref = ref
    ctx.db.commit()
    notify.email_owner(f"Check {PAYMENTS[o.payment_method]['label']} payment for order #{o.id}",
                       f"The customer sent TrxID {ref} for ৳{o.total}. Check it in your account, then mark the order paid in the dashboard.")
    return {"status": "RECORDED_NOT_VERIFIED",
            "instruction": "Thank them and say the team will check the payment and confirm here. Don't say it's confirmed."}


def get_order_status(ctx: Ctx, order_id: int | None = None, phone: str | None = None) -> dict:
    if order_id:
        o = ctx.db.get(Order, int(order_id))
        return order_dict(o) if o else {"error": f"No order #{order_id}"}
    p = normalize_phone(phone or "")
    if not p:
        return {"error": "Need an order number or a valid phone number"}
    orders = ctx.db.scalars(select(Order).where(Order.phone == p).order_by(Order.id.desc()).limit(3)).all()
    return {"orders": [order_dict(o) for o in orders]} if orders else {"error": f"No orders for {p}"}


def get_business_info(ctx: Ctx, topic: str) -> dict:
    b = BUSINESS
    info = {"hours": b["hours"], "location": b["location"], "delivery": DELIVERY,
            "payment": PAYMENTS, "returns": b["return_policy"], "about": b["about"]}
    return {topic: info[topic]} if topic in info else {"error": f"topic must be one of {list(info)}"}


def escalate_to_human(ctx: Ctx, reason: str) -> dict:
    notify.flag_for_human(ctx.db, ctx.conv, reason, stop_bot=True)
    return {"status": "escalated", "instruction": "Tell the customer briefly that a team member will reply here soon."}


def product_photo(product_id: str) -> Path | None:
    return next((f for f in (MEDIA_DIR / "products").glob(f"{product_id}.*") if is_photo(f.name)), None)


def is_photo(path: str) -> bool:
    return path.lower().endswith((".jpg", ".jpeg", ".png"))  # the image types WhatsApp accepts


def send_product_photo(ctx: Ctx, product_id: str) -> dict:
    from . import pipeline  # avoid import cycle

    p = ctx.db.get(Product, product_id)
    photo = p and product_photo(p.id)
    if not photo:
        return {"error": "No photo for this product. Describe it in words instead."}
    caption = f"{p.name_en} – ৳{p.price}"
    try:
        pipeline.deliver(ctx.conv.channel, ctx.conv.phone, caption, image=photo)
    except Exception:
        log.exception("sending photo %s failed", photo.name)
        return {"error": "Could not send the photo right now. Describe the product in words instead."}
    ctx.db.add(Message(conversation_id=ctx.conv.id, role="assistant", text=caption, audio_path=f"products/{photo.name}"))
    ctx.db.commit()
    return {"status": "SENT", "instruction": "The photo is already in the chat. Don't describe it at length or send a link."}


FUNCTIONS = {f.__name__: f for f in [search_products, get_product, send_product_photo, create_order, record_payment,
                                     get_order_status, get_business_info, escalate_to_human]}

_item = {"type": "object", "properties": {
    "product_id": {"type": "string"}, "size": {"type": "string"}, "colour": {"type": "string"},
    "quantity": {"type": "integer"}}, "required": ["product_id", "size", "colour", "quantity"]}

SPECS = [
    {"name": "search_products",
     "description": "Search the catalogue. Query can be English, Bangla or Banglish, e.g. 'panjabi', 'পাঞ্জাবি', 'blue kurti'. "
                    "Returns price, sizes, colours and stock for each size / colour (only offer combinations with stock).",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_product", "description": "Get one product by id.",
     "input_schema": {"type": "object", "properties": {"product_id": {"type": "string"}}, "required": ["product_id"]}},
    {"name": "send_product_photo",
     "description": "Send the customer a photo of a product (only products with has_photo=true). Use when they ask to see it or for a picture.",
     "input_schema": {"type": "object", "properties": {"product_id": {"type": "string"}}, "required": ["product_id"]}},
    {"name": "create_order",
     "description": "Two steps. confirmed=false with all details: validates stock and returns the official summary and total "
                    "(nothing is placed). confirmed=true (no other fields): places the last quoted order; only call it after "
                    "the customer said yes to that summary in a later message.",
     "input_schema": {"type": "object", "properties": {
         "confirmed": {"type": "boolean"},
         "customer_name": {"type": "string"}, "phone": {"type": "string"}, "address": {"type": "string"},
         "delivery_area": {"type": "string", "enum": list(DELIVERY)},
         "payment_method": {"type": "string", "enum": list(PAYMENTS)},
         "items": {"type": "array", "items": _item}}, "required": ["confirmed"]}},
    {"name": "record_payment",
     "description": "Save the bKash/Nagad transaction ID (TrxID) a customer sends after paying for an order placed in this chat. "
                    "The team checks it; it is not confirmed until then.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}, "transaction_id": {"type": "string"}},
                      "required": ["order_id", "transaction_id"]}},
    {"name": "get_order_status", "description": "Look up an order by order number, or recent orders by phone number.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}, "phone": {"type": "string"}}}},
    {"name": "get_business_info", "description": "Shop facts: opening hours, location, delivery charges/times, payment methods, return policy.",
     "input_schema": {"type": "object", "properties": {
         "topic": {"type": "string", "enum": ["hours", "location", "delivery", "payment", "returns", "about"]}},
         "required": ["topic"]}},
    {"name": "escalate_to_human",
     "description": "Hand the chat to a human. Use when the customer asks for a person, is angry or upset, or asks something you can't answer from the tools.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]


def run_tool(ctx: Ctx, name: str, args: dict) -> dict:
    if name not in FUNCTIONS:
        return {"error": f"Unknown tool {name}"}
    try:
        return FUNCTIONS[name](ctx, **args)
    except TypeError as e:  # bad/missing arguments from the model
        return {"error": str(e)}
