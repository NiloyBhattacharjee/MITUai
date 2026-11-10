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
