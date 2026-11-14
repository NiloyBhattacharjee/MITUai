import base64
import hashlib
import hmac
from urllib.parse import parse_qs

from fastapi.testclient import TestClient

from app import pipeline
from app.db import Conversation, Message
from app.main import app

client = TestClient(app)
SHOP_NUMBER = "+15550001111"


def post_twilio(path, params, token="twilio-secret"):
    data = f"https://shop.test{path}" + "".join(k + params[k] for k in sorted(params))
    sig = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    return client.post(path, data=params, headers={"X-Twilio-Signature": sig})


def sms(sid="SM1", body="are you open?"):
    return {"MessageSid": sid, "From": "+8801712345678", "To": SHOP_NUMBER, "Body": body, "NumMedia": "0"}


def sent_sms(graph):
    return [parse_qs(r.content.decode()) for r in graph if r.url.path.endswith("/Messages.json")]


def test_sms_reaches_mitu_once_and_is_answered_by_sms(db, fake_llm, graph):
    assert post_twilio("/twilio/sms", sms(), token="wrong").status_code == 403
    for _ in range(2):  # Twilio retry
        assert post_twilio("/twilio/sms", sms()).status_code == 200
    assert len(fake_llm.calls) == 1 and fake_llm.calls[0][-1]["text"].endswith("are you open?")
    assert sent_sms(graph) == [{"From": [SHOP_NUMBER], "To": ["+8801712345678"], "Body": ["ok"]}]
    assert graph[0].url.path == "/2010-04-01/Accounts/AC-test/Messages.json"
    assert db.get(Conversation, "little-fern:phone-+8801712345678").channel == "sms"


def test_call_greets_listens_and_speaks_the_reply(db, fake_llm, graph):
    call = {"CallSid": "CA1", "From": "+8801712345678", "To": SHOP_NUMBER}
    assert post_twilio("/twilio/voice", call, token="wrong").status_code == 403
    r = post_twilio("/twilio/voice", call)
    assert r.headers["content-type"].startswith("application/xml")
    assert '<Gather input="speech" action="/twilio/voice/turn"' in r.text and "Little Fern" in r.text

    fake_llm.turns = [lambda h, call: "A latte is $5 & up."]
    r = post_twilio("/twilio/voice/turn", {**call, "SpeechResult": "how much is a latte?"})
    assert "<Say" in r.text and "A latte is $5 &amp; up." in r.text and "<Gather" in r.text
    msgs = db.query(Message).filter(Message.conversation_id == "little-fern:phone-+8801712345678").order_by(Message.id).all()
    assert [(m.role, m.is_voice) for m in msgs] == [("assistant", False), ("user", True), ("assistant", False)]
    assert len(fake_llm.calls) == 1  # the greeting counts as the introduction, so the history starts with the customer


def test_call_ends_after_handoff_and_follow_ups_go_by_sms(db, fake_llm, graph):
    call = {"CallSid": "CA2", "From": "+8801799999999", "To": SHOP_NUMBER}
    post_twilio("/twilio/voice", call)
    fake_llm.turns = [lambda h, call: call("escalate_to_human", {"reason": "wants a person"}) and "Someone will call you back."]
    r = post_twilio("/twilio/voice/turn", {**call, "SpeechResult": "can I talk to a person"})
    assert "Someone will call you back." in r.text and "<Hangup/>" in r.text and "<Gather" not in r.text

    conv = db.get(Conversation, "little-fern:phone-+8801799999999", populate_existing=True)
    assert pipeline.send_update(db, conv, "Your order #7 is ready.") is None  # no 24-hour window on SMS
    assert sent_sms(graph)[-1]["Body"] == ["Your order #7 is ready."]
