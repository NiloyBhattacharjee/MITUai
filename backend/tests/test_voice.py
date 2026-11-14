import json

import pytest
from sqlalchemy import select

from app import pipeline, voice, whatsapp
from app.tenancy import cfg
from app.db import Message
from app.voice import Transcript


def test_media_download(graph):
    data, mime = whatsapp.download_media("media-1")
    assert data == b"OGGDATA" and mime.startswith("audio/ogg")
    assert all(r.headers["Authorization"] == "Bearer tok" for r in graph)
    assert [r.url.host for r in graph] == ["graph.facebook.com", "lookaside.fbsbx.com"]


class FakeSTT:
    def __init__(self, t):
        self.t = t

    def transcribe(self, audio_bytes):
        return self.t


@pytest.fixture
def stt(monkeypatch):
    monkeypatch.setattr(voice, "to_wav", lambda data, ext: b"WAV")
    monkeypatch.setattr(voice, "wav_seconds", lambda wav: 5.0)

    def use(t):
        monkeypatch.setattr(voice, "get_stt", lambda: FakeSTT(t))
    return use


def wa_voice():
    return {"from": "8801712345678", "id": "wamid.v1", "type": "audio", "audio": {"id": "media-1", "voice": True}}


def test_transcript_goes_through_agent(db, cafe, fake_llm, graph, stt):
    stt(Transcript("Can I get a large flat white?", "en", 0.92))
    fake_llm.turns = [lambda history, call: "Sure! A large flat white is $5.80."]
    pipeline.handle_whatsapp_message(cafe, wa_voice())

    assert fake_llm.calls[0][-1]["text"].endswith("] [voice note] Can I get a large flat white?")
    user = db.scalars(select(Message).where(Message.role == "user")).one()
    assert user.is_voice and user.audio_path.endswith(".ogg") and user.text == "Can I get a large flat white?"
    assert any(b"$5.80" in r.content for r in graph)


def test_low_confidence_asks_to_repeat(db, cafe, fake_llm, graph, stt):
    stt(Transcript("uh mm", "en", 0.2))
    pipeline.handle_whatsapp_message(cafe, wa_voice())
    assert fake_llm.calls == []  # never guess
    bot = db.scalars(select(Message).where(Message.role == "assistant")).one()
    assert bot.text == cfg()["messages"]["voice_unclear"]


def test_long_voice_note(db, cafe, fake_llm, graph, stt, monkeypatch):
    monkeypatch.setattr(voice, "wav_seconds", lambda wav: 150.0)
    pipeline.handle_whatsapp_message(cafe, wa_voice())
    assert fake_llm.calls == []
    assert db.scalars(select(Message).where(Message.role == "assistant")).one().text == cfg()["messages"]["voice_too_long"]


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def openai_stt(monkeypatch):
    """voice.OpenAISTT with httpx.post replaced: returns the form fields it sent and lets a test set the reply."""
    sent = {}

    def post(url, **kw):
        sent.update(url=url, **kw)
        return FakeResponse(sent.pop("reply"))
    monkeypatch.setattr(voice.httpx, "post", post)
    return sent


def test_bangla_voice_note_uses_the_newer_model_with_the_business_language(db, cafe, openai_stt, monkeypatch):
    monkeypatch.delenv("OPENAI_STT_MODEL", raising=False)  # the default is the model that handles Bangla
    monkeypatch.setitem(cfg(), "voice_language", "bn")
    openai_stt["reply"] = {"text": "নীল কুর্তিটা কি L সাইজে আছে?", "logprobs": [{"logprob": -0.05}, {"logprob": -0.2}, {"logprob": -0.1}]}
    t = voice.get_stt().transcribe(b"WAV")
    form = openai_stt["data"]
    assert form["model"] == "gpt-4o-transcribe" and form["response_format"] == "json"
    assert form["include[]"] == "logprobs" and form["language"] == "bn"
    assert t.text == "নীল কুর্তিটা কি L সাইজে আছে?" and t.language == "bn"
    assert 0.85 < t.confidence < 0.95  # the mean of each token's probability


def test_without_a_language_hint_the_model_detects_it(db, cafe, openai_stt, monkeypatch):
    monkeypatch.delenv("OPENAI_STT_MODEL", raising=False)
    openai_stt["reply"] = {"text": "Còn size M không?"}
    t = voice.get_stt().transcribe(b"WAV")
    assert "language" not in openai_stt["data"]
    assert t.text == "Còn size M không?" and t.confidence is None  # no scores: the transcript is used as is


def test_whisper_still_works_when_chosen(db, cafe, openai_stt, monkeypatch):
    monkeypatch.setenv("OPENAI_STT_MODEL", "whisper-1")
    openai_stt["reply"] = {"text": "a flat white", "language": "english", "segments": [{"avg_logprob": -0.1, "no_speech_prob": 0.0}]}
    t = voice.get_stt().transcribe(b"WAV")
    assert openai_stt["data"]["response_format"] == "verbose_json" and "include[]" not in openai_stt["data"]
    assert t.text == "a flat white" and t.language == "english" and t.confidence == pytest.approx(0.905, abs=0.01)


def sent_types(graph) -> list[str]:
    """The kinds of WhatsApp messages sent, in order (read receipts and uploads left out)."""
    bodies = [json.loads(r.content) for r in graph if r.url.path.endswith("/messages")]
    return [b["type"] for b in bodies if "type" in b]


def test_voice_note_gets_a_voice_reply_when_the_business_turns_it_on(db, cafe, fake_llm, graph, stt, monkeypatch):
    monkeypatch.setitem(cfg(), "voice_replies", True)
    spoken = []
    monkeypatch.setattr(voice, "speak", lambda text: spoken.append(text) or b"OGGREPLY")
    stt(Transcript("Table for two tonight?", "en", 0.9))
    fake_llm.turns = [lambda history, call: "Yes, we have a table for two at 7pm."]
    pipeline.handle_whatsapp_message(cafe, wa_voice())

    assert spoken == ["Yes, we have a table for two at 7pm."]
    assert sent_types(graph) == ["audio", "text"]  # the voice note, then the same words to read
    upload = next(r for r in graph if r.url.path.endswith("/media"))
    assert b"audio/ogg" in upload.content and b"OGGREPLY" in upload.content
    bot = db.scalars(select(Message).where(Message.role == "assistant")).one()
    assert bot.audio_path.endswith(".ogg") and (pipeline.MEDIA_DIR / bot.audio_path).read_bytes() == b"OGGREPLY"


def test_typed_messages_and_businesses_without_voice_replies_get_text(db, cafe, fake_llm, graph, stt, monkeypatch):
    monkeypatch.setattr(voice, "speak", lambda text: pytest.fail("no voice reply expected"))
    stt(Transcript("Table for two tonight?", "en", 0.9))
    pipeline.handle_whatsapp_message(cafe, wa_voice())  # voice_replies is off for the café
    monkeypatch.setitem(cfg(), "voice_replies", True)
    pipeline.handle_whatsapp_message(cafe, {"from": "8801712345678", "id": "wamid.t1", "type": "text", "text": {"body": "hi"}})
    assert sent_types(graph) == ["text", "text"]


def test_a_failed_voice_reply_still_sends_the_text(db, cafe, fake_llm, graph, stt, monkeypatch):
    monkeypatch.setitem(cfg(), "voice_replies", True)

    def broken(text):
        raise RuntimeError("TTS down")
    monkeypatch.setattr(voice, "speak", broken)
    stt(Transcript("Table for two tonight?", "en", 0.9))
    pipeline.handle_whatsapp_message(cafe, wa_voice())
    assert sent_types(graph) == ["text"]
