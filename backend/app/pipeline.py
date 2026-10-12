"""One path for every incoming message, whichever channel it came from."""
import logging
import mimetypes
import time
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update

from . import agent, messenger, notify, voice, whatsapp
from .config import BUSINESS, MEDIA_DIR, PUBLIC_URL, STT_MIN_CONFIDENCE, VOICE_MAX_SECONDS, VOICE_REPLY
from .db import Message, SeenWhatsAppMessage, Session, now

log = logging.getLogger(__name__)
WHATSAPP_WINDOW = timedelta(hours=24)  # Meta's rule on WhatsApp, Messenger and Instagram: after it, only templates/tags


def deliver(channel: str, phone: str, text: str, ogg: bytes | None = None, image: Path | None = None):
    if channel == "whatsapp":
        if image:  # text becomes the photo's caption
            whatsapp.send_image(phone, whatsapp.upload_media(image.read_bytes(), mimetypes.guess_type(image.name)[0]), text)
        else:
            whatsapp.send_text(phone, text)
        if ogg:
            whatsapp.send_audio(phone, whatsapp.upload_media(ogg))
    elif channel in messenger.CHANNELS.values():
        if image:  # no captions on Messenger/Instagram images: photo first, then the text
            if not PUBLIC_URL:
                raise RuntimeError("PUBLIC_URL is not set, so Messenger/Instagram can't fetch the photo")
            messenger.send_image(phone, f"{PUBLIC_URL}/media/{image.relative_to(MEDIA_DIR).as_posix()}")
        messenger.send_text(phone, text)
        # ponytail: voice replies (ogg) are WhatsApp-only; Messenger/Instagram get the text. Add when VOICE_REPLY is used there.
    # simulator: nothing to push, the page polls the DB


def last_customer_message_at(db, conv_id: str):
    return db.scalar(select(func.max(Message.created_at)).where(Message.conversation_id == conv_id, Message.role == "user"))


def window_open(channel: str, last_customer_message) -> bool:
    """Meta only allows free-form messages within 24 hours of the customer's last one."""
    return channel == "sim" or (last_customer_message is not None and now() - last_customer_message < WHATSAPP_WINDOW)


def send_update(db, conv, text: str) -> str | None:
    """A message the shop starts (e.g. order status). Returns why it wasn't sent, or None if it was."""
    if not window_open(conv.channel, last_customer_message_at(db, conv.id)):
        return "Customer not notified: WhatsApp only allows messages within 24 hours of the customer's last message."
    try:
        deliver(conv.channel, conv.phone, text)
    except Exception:
        log.exception("status update to %s failed", conv.id)
        return "Customer not notified: sending the WhatsApp message failed."
    agent.add_message(db, conv, "assistant", text)
    return None


def handle_text(conv_id: str, channel: str, phone: str, text: str):
    reply = agent.respond(conv_id, channel, phone, text)
    if reply:
        deliver(channel, phone, reply.text)


PHOTO_EXT = {"image/jpeg": "jpg", "image/png": "png"}  # what WhatsApp sends and every LLM provider reads


def handle_photo(conv_id: str, channel: str, phone: str, data: bytes, mime: str, caption: str = ""):
    """A customer's photo, e.g. a screenshot of a product post or of a bKash payment. The LLM sees the image."""
    ext = PHOTO_EXT.get(mime.split(";")[0].strip())
    if not ext:
        return canned_reply(conv_id, channel, phone, "unsupported_media", f"[{mime or 'file'}]")
    fname = f"{uuid.uuid4().hex}.{ext}"  # unguessable: /media is served without auth
    (MEDIA_DIR / fname).write_bytes(data)
    reply = agent.respond(conv_id, channel, phone, caption, audio_path=fname)
    if reply:
        deliver(channel, phone, reply.text)


def canned_reply(conv_id, channel, phone, key: str, user_text: str, is_voice=False, audio_path=None, flag: str | None = None):
    """Store the customer's message and answer with a fixed message from business.yaml (no LLM)."""
    with Session() as db:
        conv = agent.get_conversation(db, conv_id, channel, phone)
        agent.add_message(db, conv, "user", user_text, is_voice, audio_path)
        if flag:
            notify.flag_for_human(db, conv, flag)
        if not conv.bot_enabled:
            return
        text = BUSINESS["messages"][key]
        agent.add_message(db, conv, "assistant", text)
    deliver(channel, phone, text)


def handle_voice(conv_id: str, channel: str, phone: str, audio: bytes, ext: str):
    t0 = time.perf_counter()
    timings = {}

    def lap(step):
        nonlocal t0
        timings[step] = round(time.perf_counter() - t0, 2)
        t0 = time.perf_counter()

    fname = f"{uuid.uuid4().hex}.{ext}"  # unguessable: /media is served without auth
    (MEDIA_DIR / fname).write_bytes(audio)
    try:
        wav = voice.to_wav(audio, ext)
        lap("convert")
        if voice.wav_seconds(wav) > VOICE_MAX_SECONDS:
            return canned_reply(conv_id, channel, phone, "voice_too_long", "", True, fname)
        tr = voice.get_stt().transcribe(wav)
        lap("transcribe")
    except Exception:
        log.exception("voice processing failed for %s", conv_id)
        return canned_reply(conv_id, channel, phone, "error", "", True, fname, flag="Voice note could not be processed")
    log.info("voice transcript (%s, conf=%s): %s", tr.language, tr.confidence, tr.text)

    if not tr.text or (tr.confidence is not None and tr.confidence < STT_MIN_CONFIDENCE):
        return canned_reply(conv_id, channel, phone, "voice_unclear", tr.text, True, fname)

    reply = agent.respond(conv_id, channel, phone, tr.text, is_voice=True, audio_path=fname)
    lap("agent")
    if not reply:
        return
    ogg = None
    if VOICE_REPLY:
        try:
            audio_out, out_ext = voice.get_tts().synthesize(reply.text)
            ogg = voice.to_ogg_opus(audio_out, out_ext)
            out_name = f"{uuid.uuid4().hex}.ogg"
            (MEDIA_DIR / out_name).write_bytes(ogg)
            with Session() as db:
                db.merge(reply).audio_path = out_name
                db.commit()
            lap("tts")
        except Exception:
            log.exception("voice reply failed; sending text only")
    deliver(channel, phone, reply.text, ogg)
    lap("send")
    log.info("voice note %s timings (s): %s", conv_id, timings)


MIME_EXT = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/aac": "aac", "audio/amr": "amr", "audio/webm": "webm",
            "audio/x-m4a": "m4a", "video/mp4": "mp4"}  # Messenger voice clips often come as mp4


def handle_whatsapp_message(msg: dict):
    phone = msg["from"]
    try:
        whatsapp.mark_read(msg["id"])
        kind = msg.get("type")
        if kind == "text":
            handle_text(phone, "whatsapp", phone, msg["text"]["body"])
        elif kind == "audio":
            t = time.perf_counter()
            data, mime = whatsapp.download_media(msg["audio"]["id"])
            log.info("downloaded voice note in %.2fs", time.perf_counter() - t)
            handle_voice(phone, "whatsapp", phone, data, MIME_EXT.get(mime.split(";")[0].strip(), "ogg"))
        elif kind == "image":
            data, mime = whatsapp.download_media(msg["image"]["id"])
            handle_photo(phone, "whatsapp", phone, data, mime, msg["image"].get("caption", ""))
        else:
            canned_reply(phone, "whatsapp", phone, "unsupported_media", f"[{kind}]")
    except Exception:
        log.exception("failed to handle WhatsApp message %s", msg.get("id"))
    _mark_done(msg["id"])


def handle_meta_event(channel: str, event: dict):
    """A Messenger or Instagram message. The sender id (PSID/IGSID) is the only identity; Mitu asks for a phone when ordering."""
    sender, msg = event["sender"]["id"], event["message"]
    conv_id = f"{channel}-{sender}"
    attachments = msg.get("attachments") or []
    audio = next((a for a in attachments if a.get("type") == "audio"), None)
    image = next((a for a in attachments if a.get("type") == "image"), None)
    try:
        if msg.get("text"):
            handle_text(conv_id, channel, sender, msg["text"])
        elif audio:
            data, mime = messenger.download(audio["payload"]["url"])
            handle_voice(conv_id, channel, sender, data, MIME_EXT.get(mime.split(";")[0].strip(), "mp4"))
        elif image:
            data, mime = messenger.download(image["payload"]["url"])
            handle_photo(conv_id, channel, sender, data, mime)
        else:
            canned_reply(conv_id, channel, sender, "unsupported_media", f"[{attachments[0].get('type') if attachments else 'message'}]")
    except Exception:
        log.exception("failed to handle %s message %s", channel, msg.get("mid"))
    _mark_done(msg["mid"])


def _mark_done(message_id: str):
    # Errors count as done too (no retry loop). Only a crash or restart skips this.
    with Session() as db:
        db.execute(update(SeenWhatsAppMessage).where(SeenWhatsAppMessage.id == message_id).values(done=True))
        db.commit()


def resume_unfinished():
    """Re-run messages a crash or restart cut off. At-least-once: rarely, a customer gets a reply twice."""
    # ponytail: assumes one server process; with several workers, one would re-run messages another is still handling.
    with Session() as db:
        pending = db.execute(select(SeenWhatsAppMessage.channel, SeenWhatsAppMessage.payload)
                             .where(SeenWhatsAppMessage.done.is_(False))).all()
    for channel, payload in pending:
        log.info("resuming %s message after a restart", channel or "whatsapp")
        if channel in (None, "whatsapp"):
            handle_whatsapp_message(payload)
        else:
            handle_meta_event(channel, payload)
