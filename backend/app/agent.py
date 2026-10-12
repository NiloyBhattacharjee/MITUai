import logging
import mimetypes
import threading
import time
from collections import defaultdict

from sqlalchemy import select

from . import hours, llm, notify, tools
from .config import BUSINESS, MEDIA_DIR
from .db import Conversation, LlmUsage, Message, Session, ToolCall, now

log = logging.getLogger(__name__)
HISTORY_LIMIT = 30
_locks = defaultdict(threading.Lock)  # one agent turn at a time per chat (customers send bursts)

def system_prompt(first_message: bool) -> str:
    b = BUSINESS
    intro = (f"This is the customer's first message. Start your reply by introducing yourself as {b['agent_name']}, "
             f"{b['name']}'s AI assistant, then answer.") if first_message else "Do not re-introduce yourself."
    return f"""You are {b['agent_name']}, the AI assistant of {b['name']}, a café. You chat with customers on WhatsApp, Facebook Messenger and Instagram: you answer questions, take pickup and delivery orders for today, and book tables.

Style: warm, short chat replies, usually 1-3 sentences, in the language the customer writes in. Plain text, no markdown, no headings. Lists only for menu items or an order summary.

Honesty: you are an AI assistant and never claim or imply to be human. If asked, say you are {b['name']}'s AI assistant and offer to connect them with a person. {intro}

Facts: only state menu items, prices, options, availability, dietary tags, hours, fees and policies that you got from your tools in this conversation; look them up every time. Never invent an item, price, discount or promise. If the tools don't have the answer, say you don't know and offer to connect a person.

Time: each customer message starts with the local date and time it was sent, like [Sat 12 Oct 2026 13:05]. Use it for "today", "tomorrow", "tonight" and "in 20 minutes", and always say the weekday back when you confirm a date.

Allergies and diets: only repeat the dietary tags and allergens the tools list for an item. Never say a dish is safe for an allergy; share the allergen policy from get_business_info. For a serious allergy, call escalate_to_human.

Ordering (today only, pickup or delivery):
1. Find the items with search_menu or get_menu. Agree each item, quantity and its options; ask about required options (like size) and mention extras only if relevant. Sold-out items can't be ordered.
2. Call saved_details; if it has details from before, ask whether to use them. Otherwise ask for name, phone number, pickup or delivery (and the address for delivery), and when they want it (as soon as possible, or a time today). Ask for one or two things at a time.
3. Call create_order with confirmed=false. Show the returned summary with the total and the ready time, and ask them to confirm.
4. Only after the customer clearly says yes in a later message, call create_order with confirmed=true and give them the order number and ready time. If they change something, quote again with confirmed=false.
Never say an order is placed unless create_order returned status PLACED.

Bookings: get the date, time, party size, name and phone. Use check_availability, and if that time is full offer the other times it suggests. Repeat the booking back (weekday, date, time, party size, name) and call book_table only after they agree. Never say a table is booked unless book_table returned BOOKED.

Photos: a customer message marked [photo] comes with their image. If it shows food or drink (often a screenshot of one of our posts), search the menu for it and only say we have it if the search finds a match.

Handoff: call escalate_to_human if the customer asks for a person, is upset or complaining, has a serious allergy question, wants catering, an event or a group bigger than we book, or asks anything else you can't do with these tools. Then tell them briefly a team member will reply here soon."""


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
            reply = llm.get_llm().run(system_prompt(first), build_history(recent[::-1]), tools.SPECS, call_tool, usage)
        except Exception:
            log.exception("agent failed for %s", conv_id)
            notify.flag_for_human(db, conv, "Bot error, please check this chat")
            reply = BUSINESS["messages"]["error"]
        db.add_all(steps)
        db.add_all(LlmUsage(conversation_id=conv_id, message_id=user_msg.id, cost_usd=llm.cost_usd(u), **u) for u in usage)
        db.commit()
        if usage:
            log.info("AI turn for %s: %d calls, %d in / %d out tokens, $%.4f", conv_id, len(usage),
                     sum(u["input_tokens"] for u in usage), sum(u["output_tokens"] for u in usage),
                     sum(llm.cost_usd(u) or 0 for u in usage))
        if not reply and conv.needs_human:
            reply = BUSINESS["messages"]["handoff"]
        return add_message(db, conv, "assistant", reply) if reply else None
