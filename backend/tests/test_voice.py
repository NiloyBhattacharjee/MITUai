import pytest
from sqlalchemy import select

from app import pipeline, voice, whatsapp
from app.config import BUSINESS
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


def test_transcript_goes_through_agent(db, fake_llm, graph, stt):
    stt(Transcript("Can I get a large flat white?", "en", 0.92))
    fake_llm.turns = [lambda history, call: "Sure! A large flat white is $5.80."]
    pipeline.handle_whatsapp_message(wa_voice())

    assert fake_llm.calls[0][-1]["text"].endswith("] [voice note] Can I get a large flat white?")
    user = db.scalars(select(Message).where(Message.role == "user")).one()
    assert user.is_voice and user.audio_path.endswith(".ogg") and user.text == "Can I get a large flat white?"
    assert any(b"$5.80" in r.content for r in graph)


def test_low_confidence_asks_to_repeat(db, fake_llm, graph, stt):
    stt(Transcript("uh mm", "en", 0.2))
    pipeline.handle_whatsapp_message(wa_voice())
    assert fake_llm.calls == []  # never guess
    bot = db.scalars(select(Message).where(Message.role == "assistant")).one()
    assert bot.text == BUSINESS["messages"]["voice_unclear"]


def test_long_voice_note(db, fake_llm, graph, stt, monkeypatch):
    monkeypatch.setattr(voice, "wav_seconds", lambda wav: 150.0)
    pipeline.handle_whatsapp_message(wa_voice())
    assert fake_llm.calls == []
    assert db.scalars(select(Message).where(Message.role == "assistant")).one().text == BUSINESS["messages"]["voice_too_long"]
