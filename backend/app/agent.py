import logging
import mimetypes
import threading
import time
from collections import defaultdict

from sqlalchemy import select

from . import hours, llm, notify, tools
from .config import MEDIA_DIR
from .db import Conversation, LlmUsage, Message, Session, ToolCall, now
from .tenancy import cfg

log = logging.getLogger(__name__)
HISTORY_LIMIT = 30
_locks = defaultdict(threading.Lock)  # one agent turn at a time per chat (customers send bursts)

def system_prompt(first_message: bool) -> str:
    """Built from what this business does (see tools.sells/tables/appts), so a salon's assistant never offers delivery."""
    b = cfg()
    kind, today_only = b.get("kind", "a business"), not tools.days_ahead()
    intro = (f"This is the customer's first message. Start your reply by introducing yourself as {b['agent_name']}, "
             f"{b['name']}'s AI assistant, then answer.") if first_message else "Do not re-introduce yourself."
    jobs = ["answer questions"]
    if tools.sells():
        jobs.append("take pickup and delivery orders" + (" for today" if today_only else ""))
    if tools.tables():
        jobs.append("book tables")
    if tools.appts():
        jobs.append("book appointments")
    parts = [f"""You are {b['agent_name']}, the AI assistant of {b['name']}, {kind}. You chat with customers on WhatsApp, Facebook Messenger and Instagram: you {', '.join(jobs[:-1]) + ' and ' + jobs[-1] if len(jobs) > 1 else jobs[0]}.

Style: warm, short chat replies, usually 1-3 sentences, in the language the customer writes in. Plain text, no markdown, no headings. Lists only for items or an order summary.

Honesty: you are an AI assistant and never claim or imply to be human. If asked, say you are {b['name']}'s AI assistant and offer to connect them with a person. {intro}

Facts: only state items, prices, options, availability, hours, fees and policies that you got from your tools in this conversation; look them up every time. Never invent an item, price, discount or promise. If the tools don't have the answer, say you don't know and offer to connect a person.

Time: each customer message starts with the local date and time it was sent, like [Sat 12 Oct 2026 13:05]. Use it for "today", "tomorrow", "tonight" and "in 20 minutes", and always say the weekday back when you confirm a date."""]
    if b.get("allergen_note"):
        parts.append("Allergies and diets: only repeat the dietary tags and allergens the tools list for an item. Never say a dish is safe for an allergy; share the allergen policy from get_business_info. For a serious allergy, call escalate_to_human.")
    if tools.sells():
        when = "as soon as possible, or a time today" if today_only else f"as soon as possible, or a day and time up to {tools.days_ahead()} days ahead"
        parts.append(f"""Ordering ({'today only, ' if today_only else ''}pickup or delivery):
1. Find the items with search_menu or get_menu. Agree each item, quantity and its options; ask about required options (like size) and mention extras only if relevant. Sold-out items can't be ordered, and you can't sell more than are left.
2. Call saved_details; if it has details from before, ask whether to use them. Otherwise ask for name, phone number, pickup or delivery (and the address for delivery), how they'll pay if there's a choice, and when they want it ({when}). Ask for one or two things at a time.
3. Call create_order with confirmed=false. Show the returned summary with the total and the ready time, and ask them to confirm.
4. Only after the customer clearly says yes in a later message, call create_order with confirmed=true and give them the order number and ready time. If they change something, quote again with confirmed=false.
Never say an order is placed unless create_order returned status PLACED.""")
    if tools.tables():
        parts.append("Bookings: get the date, time, party size, name and phone. Use check_availability, and if that time is full offer the other times it suggests. Repeat the booking back (weekday, date, time, party size, name) and call book_table only after they agree. Never say a table is booked unless book_table returned BOOKED. For a group bigger than we book, get the same details and call request_approval with kind large_booking.")
    if tools.appts():
        parts.append("Appointments: find the service with search_menu or get_menu (services have minutes). Ask which day suits them and whether they'd like someone in particular, then call appointment_times and offer two or three of the free times. Get their name and phone (call saved_details first). Repeat the service, weekday, date, time and who it's with, and call book_appointment only after they agree. Never say it's booked unless book_appointment returned BOOKED. To move an appointment, book the new time first, then cancel the old one.")
    parts.append("Photos: a customer message marked [photo] comes with their image. If it shows something we might sell or do (often a screenshot of one of our posts), search for it and only say we have it if the search finds a match.")
    parts.append("Requests: if a customer asks for a refund or remake, a discount or freebie, or another exception, don't promise or refuse it yourself: call request_approval with a one-line summary (and the order number if there is one), then tell them the team will reply here.")
    parts.append("Handoff: call escalate_to_human if the customer asks for a person, is upset or angry, has a serious allergy question, wants a large or custom order or an event, or asks anything else you can't do with these tools. Then tell them briefly a team member will reply here soon.")
    return "\n\n".join(parts)


def get_conversation(db, conv_id: str, channel: str, phone: str) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if not conv:
        conv = Conversation(id=conv_id, channel=channel, phone=phone)
        db.add(conv)
        db.commit()
    return conv


def add_message(db, conv: Conversation, role: str, text: str, is_voice=False, audio_path=None) -> Message:
    m = Message(conversation_id=conv.id, role=role, text=text, is_voice=is_voice, audio_path=audio_path)
    db.add(m)
    conv.updated_at = now()
    db.commit()
    return m


def _image(path: str) -> list[dict]:
    f = MEDIA_DIR / path
    return [{"mime": mimetypes.guess_type(f.name)[0], "data": f.read_bytes()}] if f.is_file() else []


def build_history(messages: list[Message]) -> list[dict]:
    """Staff replies count as the café (assistant). Merge consecutive same-role turns; start with the user.
    Customer messages get their local send time, so the model knows what "today" and "in 20 minutes" mean
    (it's part of the stored message, so the cached prompt prefix stays the same from turn to turn).
    The customer's own photos go to the LLM as images; the menu photos the café sent stay a text note."""
    out = []
    for m in messages:
        role = "user" if m.role == "user" else "assistant"
        photo = bool(m.audio_path) and tools.is_photo(m.audio_path)
        text = (f"[voice note] {m.text}" if m.is_voice
                else f"[photo] {m.text}" if photo and role == "user"
                else f"[photo sent] {m.text}" if photo else m.text) or "(empty)"
        if role == "user":
            sent = hours.to_local(m.created_at)
            text = f"[{sent:%a} {sent.day} {sent:%b %Y %H:%M}] {text}"
        images = _image(m.audio_path) if photo and role == "user" else []
        if out and out[-1]["role"] == role:
            out[-1]["text"] += "\n" + text
            out[-1]["images"] += images
        else:
            out.append({"role": role, "text": text, "images": images})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def respond(conv_id: str, channel: str, phone: str, text: str, is_voice=False, audio_path=None) -> Message | None:
    """Store the customer's message and, if the bot is on for this chat, return the stored bot reply."""
    with _locks[conv_id], Session() as db:
        conv = get_conversation(db, conv_id, channel, phone)
        first = db.scalar(select(Message.id).where(Message.conversation_id == conv_id).limit(1)) is None
        user_msg = add_message(db, conv, "user", text, is_voice, audio_path)
        if not conv.bot_enabled:
            return None

        recent = db.scalars(select(Message).where(Message.conversation_id == conv_id)
                            .order_by(Message.id.desc()).limit(HISTORY_LIMIT)).all()
        ctx = tools.Ctx(db, conv, user_msg.id)
        usage, steps = [], []  # filled per API call / tool call, even if a later one fails

        def call_tool(name, args):
            t = time.perf_counter()
            result = tools.run_tool(ctx, name, args)
            steps.append(ToolCall(message_id=user_msg.id, name=name, args=args, result=result, ms=round((time.perf_counter() - t) * 1000)))
            return result

        try:
            reply = llm.get_llm().run(system_prompt(first), build_history(recent[::-1]), tools.specs(), call_tool, usage)
        except Exception:
            log.exception("agent failed for %s", conv_id)
            notify.flag_for_human(db, conv, "Bot error, please check this chat")
            reply = cfg()["messages"]["error"]
        db.add_all(steps)
        db.add_all(LlmUsage(conversation_id=conv_id, message_id=user_msg.id, cost_usd=llm.cost_usd(u), **u) for u in usage)
        db.commit()
        if usage:
            log.info("AI turn for %s: %d calls, %d in / %d out tokens, $%.4f", conv_id, len(usage),
                     sum(u["input_tokens"] for u in usage), sum(u["output_tokens"] for u in usage),
                     sum(llm.cost_usd(u) or 0 for u in usage))
        if not reply and conv.needs_human:
            reply = cfg()["messages"]["handoff"]
        return add_message(db, conv, "assistant", reply) if reply else None
