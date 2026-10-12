import logging
import mimetypes
import re
import threading
from collections import defaultdict

from sqlalchemy import select

from . import llm, notify, tools
from .config import BUSINESS, MEDIA_DIR
from .db import Conversation, LlmUsage, Message, Session, now

log = logging.getLogger(__name__)
HISTORY_LIMIT = 30
_locks = defaultdict(threading.Lock)  # one agent turn at a time per chat (customers send bursts)

BANGLISH = {"ami", "apni", "apnar", "amar", "ache", "achhe", "ase", "koto", "dam", "bhai", "vai", "apu", "kothay",
            "chai", "hobe", "korbo", "korte", "kemon", "valo", "bhalo", "taka", "ki", "na", "ta", "deben",
            "dite", "pathan", "lagbe", "nibo", "kinbo", "thik", "kalo", "lal", "nil", "sada", "jabe", "kobe"}


def detect_language(text: str) -> str:
    # ponytail: keyword heuristic for the dashboard label only; the LLM mirrors the language itself.
    if re.search(r"[ঀ-৿]", text):
        return "bangla"
    words = set(re.findall(r"[a-z]+", text.lower()))
    hits = len(words & BANGLISH)
    return "banglish" if hits and hits / len(words) >= 0.2 else "english"


def system_prompt(first_message: bool) -> str:
    b = BUSINESS
    intro = (f"This is the customer's first message. Start your reply by introducing yourself as {b['agent_name']}, "
             f"{b['name']}'s AI assistant, then answer.") if first_message else "Do not re-introduce yourself."
    return f"""You are {b['agent_name']}, the AI customer service assistant of {b['name']}, a small online clothing shop in Dhaka, Bangladesh. You chat with customers on WhatsApp, Facebook Messenger and Instagram.

Language: reply in the same language and script as the customer's latest message. Bangla script -> Bangla script. English -> English. Banglish (Bangla written in English letters) -> Banglish. Mixed -> mirror their mix. Use polite everyday Bangla with "apni"/"আপনি", like a friendly shop assistant, never formal textbook Bangla.

Style: short chat replies, usually 1-3 sentences. Plain text, no markdown, no headings. No bullet lists except when listing products or an order summary.

Honesty: you are an AI assistant and never claim or imply to be human. If asked, say you are {b['name']}'s AI assistant and offer to connect them with a person. {intro}

Facts: only state product names, prices, stock, sizes, colours, delivery charges, hours and policies that you got from your tools in this conversation; look them up every time. Never invent a product, price, discount or promise. If the tools don't have the answer, say you don't know and offer to connect a human. Prices are in taka (৳).

Ordering, in this order:
1. Find the product with search_products and agree product, size, colour and quantity (only offer size / colour combinations that have stock).
2. Ask for name, phone number, full delivery address (and whether it is inside or outside Dhaka) and payment method (cash on delivery, bKash or Nagad). Ask for one or two things at a time.
3. Call create_order with confirmed=false. Show the customer the returned summary with the total including the delivery charge, and ask them to confirm.
4. Only after the customer clearly says yes in a later message, call create_order with confirmed=true and give them the order number. If they change something, quote again with confirmed=false.
Never say an order is placed unless create_order returned status PLACED.
5. bKash/Nagad: when the customer sends their transaction ID (TrxID) after paying, call record_payment. Never say a payment is confirmed; the team checks it and confirms here.

Photos: a customer message marked [photo] comes with their image. If it shows clothing (often a screenshot of one of our posts), search the catalogue for it and only say we have it if search finds a match. If it is a bKash/Nagad payment screenshot, read the TrxID and call record_payment.

Handoff: call escalate_to_human if the customer asks for a human/real person, is angry or upset, or asks for something outside what a shop assistant can do from these tools (complaints about a received order, custom tailoring, wholesale, refunds in progress, etc). Then tell them briefly a team member will reply here soon."""


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
    """Owner replies count as the shop (assistant). Merge consecutive same-role turns; start with the user.
    The customer's own photos go to the LLM as images; the product photos the shop sent stay a text note."""
    out = []
    for m in messages:
        role = "user" if m.role == "user" else "assistant"
        photo = bool(m.audio_path) and tools.is_photo(m.audio_path)
        text = (f"[voice note] {m.text}" if m.is_voice
                else f"[photo] {m.text}" if photo and role == "user"
                else f"[photo sent] {m.text}" if photo else m.text) or "(empty)"
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
        conv.language = detect_language(text)
        db.commit()
        if not conv.bot_enabled:
            return None

        recent = db.scalars(select(Message).where(Message.conversation_id == conv_id)
                            .order_by(Message.id.desc()).limit(HISTORY_LIMIT)).all()
        ctx = tools.Ctx(db, conv, user_msg.id)
        usage = []  # filled per API call, even if a later call fails
        try:
            reply = llm.get_llm().run(system_prompt(first), build_history(recent[::-1]), tools.SPECS,
                                      lambda name, args: tools.run_tool(ctx, name, args), usage)
        except Exception:
            log.exception("agent failed for %s", conv_id)
            notify.flag_for_human(db, conv, "Bot error, please check this chat")
            reply = BUSINESS["messages"]["error"]
        if usage:
            db.add_all(LlmUsage(conversation_id=conv_id, cost_usd=llm.cost_usd(u), **u) for u in usage)
            db.commit()
            log.info("AI turn for %s: %d calls, %d in / %d out tokens, $%.4f", conv_id, len(usage),
                     sum(u["input_tokens"] for u in usage), sum(u["output_tokens"] for u in usage),
                     sum(llm.cost_usd(u) or 0 for u in usage))
        if not reply and conv.needs_human:
            reply = BUSINESS["messages"]["handoff"]
        return add_message(db, conv, "assistant", reply) if reply else None
