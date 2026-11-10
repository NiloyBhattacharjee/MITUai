"""WhatsApp Cloud API: Graph API client + webhook routes."""
import hashlib
import hmac
import json
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import config, tenancy
from .db import SeenWhatsAppMessage, Session, Shop

log = logging.getLogger(__name__)
http = httpx.Client(timeout=30)  # tests swap this for a mock transport
router = APIRouter()


def _url(path: str) -> str:
    return f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/{path}"


def _auth() -> dict:
    return {"Authorization": f"Bearer {tenancy.shop().whatsapp_token}"}  # the business in scope's own token and number


def _post_message(payload: dict) -> dict:
    r = http.post(_url(f"{tenancy.shop().whatsapp_phone_number_id}/messages"), headers=_auth(),
                  json={"messaging_product": "whatsapp", **payload})
    r.raise_for_status()
    return r.json()


def send_text(to: str, text: str):
    return _post_message({"to": to, "type": "text", "text": {"body": text}})


def send_audio(to: str, media_id: str):
    return _post_message({"to": to, "type": "audio", "audio": {"id": media_id}})


def send_image(to: str, media_id: str, caption: str):
    return _post_message({"to": to, "type": "image", "image": {"id": media_id, "caption": caption}})


def mark_read(message_id: str):
    try:
        _post_message({"status": "read", "message_id": message_id})
    except httpx.HTTPError:
        log.warning("mark_read failed for %s", message_id)


def download_media(media_id: str) -> tuple[bytes, str]:
    meta = http.get(_url(media_id), headers=_auth())
    meta.raise_for_status()
    info = meta.json()
    r = http.get(info["url"], headers=_auth())
    r.raise_for_status()
    return r.content, info.get("mime_type", "audio/ogg")


def upload_media(data: bytes, mime: str = "audio/ogg") -> str:
    r = http.post(_url(f"{tenancy.shop().whatsapp_phone_number_id}/media"), headers=_auth(),
                  data={"messaging_product": "whatsapp", "type": mime}, files={"file": (f"upload.{mime.split('/')[1]}", data, mime)})
    r.raise_for_status()
    return r.json()["id"]


def valid_signature(body: bytes, header: str | None) -> bool:
    if not config.WHATSAPP_APP_SECRET or not header:
        return False
    expected = "sha256=" + hmac.new(config.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def shop_for(column: str, value) -> Shop | None:
    """The business a webhook was sent to, by its WhatsApp phone number id or Page/Instagram id.
    With a single business, everything goes to it, so a one-business install needs no ids set up."""
    with Session() as db:
        shops = db.scalars(select(Shop)).all()
    return next((s for s in shops if value and getattr(s, column) == str(value)), shops[0] if len(shops) == 1 else None)


def first_time_seen(message_id: str, payload: dict, channel: str, shop_id: str) -> bool:
    """Store the message as pending. False if Meta already sent it."""
    with Session() as db:
        db.add(SeenWhatsAppMessage(id=message_id, payload=payload, done=False, channel=channel, shop_id=shop_id))
        try:
            db.commit()
            return True
        except IntegrityError:
            return False


@router.get("/webhook")
def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and config.WHATSAPP_VERIFY_TOKEN and q.get("hub.verify_token") == config.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403)


@router.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    from .pipeline import handle_whatsapp_message  # avoid import cycle

    body = await request.body()
    if not valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "bad signature")
    data = json.loads(body)
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if not value.get("messages"):  # "statuses" updates are ignored
                continue
            number = value.get("metadata", {}).get("phone_number_id")
            if not (shop := shop_for("whatsapp_phone_number_id", number)):
                log.warning("WhatsApp message for number %s, which no business is connected to", number)
                continue
            for msg in value["messages"]:
                if first_time_seen(msg["id"], msg, "whatsapp", shop.id):
                    background.add_task(handle_whatsapp_message, shop, msg)
    return {"ok": True}
