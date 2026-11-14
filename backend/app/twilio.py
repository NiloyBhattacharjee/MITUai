"""SMS and phone calls through Twilio: webhooks + REST API. One Twilio account for the install (TWILIO_ACCOUNT_SID and
TWILIO_AUTH_TOKEN); each business's own number (Shop.twilio_number) routes texts and calls to it. Texts and calls from one
number share a chat. Calls use Twilio's speech-to-text (<Gather input="speech">) and text-to-speech (<Say>), one webhook per
turn, so a call runs the same agent.respond() as every chat."""
import base64
import hashlib
import hmac
import logging
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from . import agent, config, tenancy, whatsapp
from .db import Conversation, Session
from .tenancy import cfg

router = APIRouter()
CHANNELS = ("sms", "call")
log = logging.getLogger(__name__)


def conv_id(shop, phone: str) -> str:
    return f"{shop.id}:phone-{phone}"


def send_sms(to: str, text: str):
    sid = config.TWILIO_ACCOUNT_SID
    r = whatsapp.http.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", auth=(sid, config.TWILIO_AUTH_TOKEN),
                           data={"From": tenancy.shop().twilio_number, "To": to, "Body": text})
    r.raise_for_status()


def valid_signature(url: str, params: dict, header: str | None) -> bool:
    """Twilio signs the URL it called plus the POST fields sorted by name, with the account's auth token."""
    if not config.TWILIO_AUTH_TOKEN or not header:
        return False
    signed = url + "".join(k + params[k] for k in sorted(params))
    expected = base64.b64encode(hmac.new(config.TWILIO_AUTH_TOKEN.encode(), signed.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, header)


async def _verified(request: Request):
    """The fields Twilio posted, and the business whose number they were sent to (None if no business has it)."""
    params = dict(await request.form())
    url = config.PUBLIC_URL + request.url.path if config.PUBLIC_URL else str(request.url)  # what Twilio called, not the proxy's URL
    if not valid_signature(url, params, request.headers.get("X-Twilio-Signature")):
        raise HTTPException(403, "bad signature")
    if not (shop := whatsapp.shop_for("twilio_number", params.get("To"))):
        log.warning("Twilio message or call for %s, which no business is connected to", params.get("To"))
    return params, shop


def _twiml(*verbs: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{"".join(verbs)}</Response>', media_type="application/xml")


def _language() -> str:
    return cfg().get("call_language", "en-US")  # what callers speak and the voice reads, e.g. bn-IN; see Twilio's language list


def _say(text: str) -> str:
    voice = f" voice={quoteattr(cfg()['call_voice'])}" if cfg().get("call_voice") else ""
    return f"<Say language={quoteattr(_language())}{voice}>{escape(text)}</Say>"


def _listen(text: str) -> str:
    """Say text, then hear the caller's answer (it arrives at /twilio/voice/turn). Silence ends the call."""
    goodbye = cfg()["messages"].get("call_goodbye", "Thanks for calling. Goodbye!")
    return (f'<Gather input="speech" action="/twilio/voice/turn" speechTimeout="auto" language={quoteattr(_language())}>'
            f"{_say(text)}</Gather>{_say(goodbye)}<Hangup/>")


@router.post("/twilio/sms")
async def sms(request: Request, background: BackgroundTasks):
    from .pipeline import handle_sms  # avoid import cycle

    params, shop = await _verified(request)
    if shop and whatsapp.first_time_seen(params["MessageSid"], params, "sms", shop.id):
        background.add_task(handle_sms, shop, params)
    return _twiml()  # the reply goes out through the API once the AI has it


def _start_call(shop, phone: str) -> Response:
    with tenancy.scope(shop), Session() as db:
        conv = agent.get_conversation(db, conv_id(shop, phone), "call", phone)
        if not conv.bot_enabled:  # staff are looking after this customer
            return _twiml(_say(cfg()["messages"]["handoff"]), "<Hangup/>")
        b = cfg()
        greeting = b["messages"].get("call_greeting") or f"Hi, you've reached {b['name']}. I'm {b['agent_name']}, an AI assistant. How can I help?"
        agent.add_message(db, conv, "assistant", greeting)  # the introduction, so the AI doesn't introduce itself again
        return _twiml(_listen(greeting))


def _call_turn(shop, phone: str, speech: str) -> Response:
    with tenancy.scope(shop):
        reply = agent.respond(conv_id(shop, phone), "call", phone, speech, is_voice=True)
        with Session() as db:
            bot_on = db.get(Conversation, conv_id(shop, phone)).bot_enabled
        if reply and bot_on:
            return _twiml(_listen(reply.text))
        return _twiml(_say(reply.text if reply else cfg()["messages"]["handoff"]), "<Hangup/>")  # handed off: staff call or text back


@router.post("/twilio/voice")
async def call(request: Request):
    params, shop = await _verified(request)
    return await run_in_threadpool(_start_call, shop, params["From"]) if shop else _twiml("<Hangup/>")


@router.post("/twilio/voice/turn")
async def call_turn(request: Request):
    # ponytail: Twilio waits 15 s for this; a slower AI turn ends the call with Twilio's error message.
    # Upgrade: answer with a short <Pause>/<Redirect> and fetch the reply on the next request, or Media Streams.
    params, shop = await _verified(request)
    if not shop:
        return _twiml("<Hangup/>")
    return await run_in_threadpool(_call_turn, shop, params["From"], params.get("SpeechResult", ""))
