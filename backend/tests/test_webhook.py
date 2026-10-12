import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import Message
from app.main import app

client = TestClient(app)


def wa_payload(msg_id="wamid.1", text="hi"):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": "8801712345678", "id": msg_id, "type": "text", "text": {"body": text}}]}}]}]}


def post_signed(payload, secret="test-secret"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def test_verify_handshake():
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_signature_check(fake_llm, graph):
    assert post_signed(wa_payload(), secret="wrong").status_code == 403
    assert client.post("/webhook", json=wa_payload()).status_code == 403  # no header
    assert post_signed(wa_payload()).status_code == 200


def test_duplicates_processed_once(db, fake_llm, graph):
    for _ in range(3):  # Meta retries
        assert post_signed(wa_payload("wamid.dup")).status_code == 200
    assert db.scalar(select(func.count()).select_from(Message).where(Message.role == "user")) == 1
    assert len(fake_llm.calls) == 1
    sent = [json.loads(r.content) for r in graph if r.method == "POST"]
    assert {"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.dup"} in sent
    assert any(s.get("type") == "text" and s["to"] == "8801712345678" for s in sent)


def test_status_updates_ignored(fake_llm, graph):
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.x", "status": "delivered"}]}}]}]}
    assert post_signed(payload).status_code == 200
    assert fake_llm.calls == [] and graph == []


def test_message_cut_off_by_restart_is_resumed(db, fake_llm, graph):
    from app.db import SeenWhatsAppMessage
    from app.pipeline import resume_unfinished
    assert post_signed(wa_payload("wamid.ok")).status_code == 200
    assert db.get(SeenWhatsAppMessage, "wamid.ok", populate_existing=True).done

    msg = wa_payload("wamid.cut", "kurti ache?")["entry"][0]["changes"][0]["value"]["messages"][0]
    db.add(SeenWhatsAppMessage(id="wamid.cut", payload=msg, done=False))  # stored, then the server died
    db.add(SeenWhatsAppMessage(id="wamid.legacy"))  # row from before the inbox existed: done is NULL, not pending
    db.commit()
    resume_unfinished()
    assert fake_llm.calls[-1][-1]["text"].endswith("kurti ache?") and len(fake_llm.calls) == 2
    assert db.get(SeenWhatsAppMessage, "wamid.cut", populate_existing=True).done
    resume_unfinished()
    assert len(fake_llm.calls) == 2  # nothing left to resume


def fb_payload(obj="page", mid="m.1", text="kalo panjabi ache?", echo=False):
    return {"object": obj, "entry": [{"messaging": [
        {"sender": {"id": "psid-42"}, "recipient": {"id": "page-1"}, "message": {"mid": mid, "text": text, "is_echo": echo}}]}]}


def post_fb(payload, secret="test-secret"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/messenger/webhook", content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def test_messenger_and_instagram_reach_mitu(db, fake_llm, graph):
    from app.db import Conversation
    assert post_fb(fb_payload(), secret="wrong").status_code == 403
    for _ in range(2):  # Meta retry
        assert post_fb(fb_payload()).status_code == 200
    assert post_fb(fb_payload(mid="m.echo", echo=True)).status_code == 200  # copy of our own reply: ignored
    assert post_fb(fb_payload("instagram", mid="ig.1", text="price koto?")).status_code == 200

    assert len(fake_llm.calls) == 2
    assert db.get(Conversation, "messenger-psid-42").channel == "messenger"
    assert db.get(Conversation, "instagram-psid-42").channel == "instagram"
    sent = [json.loads(r.content) for r in graph if r.url.path.endswith("/me/messages")]
    assert sent == [{"recipient": {"id": "psid-42"}, "messaging_type": "RESPONSE", "message": {"text": "ok"}}] * 2
    assert graph[0].headers["Authorization"] == "Bearer page-tok"


def test_messenger_photo_sent_by_public_url(db, fake_llm, graph):
    from app.config import MEDIA_DIR
    (MEDIA_DIR / "products").mkdir(exist_ok=True)
    (MEDIA_DIR / "products" / "P01.jpg").write_bytes(b"JPEGDATA")
    fake_llm.turns = [lambda h, call: call("send_product_photo", {"product_id": "P01"}) and "Eita dekhen"]
    post_fb(fb_payload(text="chobi dekhan"))
    sent = [json.loads(r.content)["message"] for r in graph if r.url.path.endswith("/me/messages")]
    assert sent[0] == {"attachment": {"type": "image", "payload": {"url": "https://shop.test/media/products/P01.jpg", "is_reusable": True}}}
    assert sent[1]["text"].startswith("Black Cotton Panjabi") and sent[2] == {"text": "Eita dekhen"}


def test_customer_photo_reaches_the_llm_as_an_image(db, fake_llm, graph):
    msg = {"from": "8801712345678", "id": "wamid.photo", "type": "image", "image": {"id": "photo-1", "caption": "eta ache?"}}
    assert post_signed({"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}).status_code == 200
    turn = fake_llm.calls[-1][-1]
    assert turn["text"] == "[photo] eta ache?" and turn["images"] == [{"mime": "image/jpeg", "data": b"OGGDATA"}]
    assert db.scalar(select(Message).where(Message.role == "user")).audio_path.endswith(".jpg")
