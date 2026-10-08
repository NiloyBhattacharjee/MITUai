"""Facebook Messenger and Instagram DMs (Meta's Messenger Platform): webhook + Send API.
Same Meta app as WhatsApp, so WHATSAPP_APP_SECRET signs these webhooks and WHATSAPP_VERIFY_TOKEN verifies them."""
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from . import config, whatsapp

router = APIRouter()
CHANNELS = {"page": "messenger", "instagram": "instagram"}  # webhook "object" -> our channel name


def _send(recipient: str, message: dict):
    r = whatsapp.http.post(whatsapp._url("me/messages"), headers={"Authorization": f"Bearer {config.PAGE_ACCESS_TOKEN}"},
                           json={"recipient": {"id": recipient}, "messaging_type": "RESPONSE", "message": message})
    r.raise_for_status()


def send_text(to: str, text: str):
    _send(to, {"text": text})


def send_image(to: str, url: str):
    """Meta downloads the image from url, so it must be public (PUBLIC_URL)."""
    _send(to, {"attachment": {"type": "image", "payload": {"url": url, "is_reusable": True}}})


def download(url: str) -> tuple[bytes, str]:
    r = whatsapp.http.get(url)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "")


@router.get("/messenger/webhook")
def verify(request: Request):
    return whatsapp.verify(request)


@router.post("/messenger/webhook")
async def receive(request: Request, background: BackgroundTasks):
    from .pipeline import handle_meta_event  # avoid import cycle

    body = await request.body()
    if not whatsapp.valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "bad signature")
    data = json.loads(body)
    channel = CHANNELS.get(data.get("object"))
    for entry in data.get("entry", []) if channel else []:
        for event in entry.get("messaging", []):
            msg = event.get("message")
            if not msg or msg.get("is_echo"):  # reads, deliveries and copies of our own replies
                continue
            if whatsapp.first_time_seen(msg["mid"], event, channel):
                background.add_task(handle_meta_event, channel, event)
    return {"ok": True}
