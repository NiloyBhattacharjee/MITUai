import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp / 'test.db'}", MEDIA_DIR=str(_tmp / "media"),
                  WHATSAPP_APP_SECRET="test-secret", WHATSAPP_VERIFY_TOKEN="verify-me", WHATSAPP_TOKEN="tok",
                  WHATSAPP_PHONE_NUMBER_ID="123", ADMIN_PASSWORD="pw", VOICE_REPLY="false", ALERT_EMAIL="",
                  STAFF="Rina:rina-pw, Karim:karim-pw", PAGE_ACCESS_TOKEN="page-tok", PUBLIC_URL="https://shop.test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from datetime import datetime  # noqa: E402

from app import hours, llm, whatsapp  # noqa: E402
from app.config import BUSINESS  # noqa: E402
from app.db import Base, Session, engine, new_item  # noqa: E402


@pytest.fixture(autouse=True)
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as s:
        s.add_all(new_item(i) for i in BUSINESS["menu"])
        s.commit()
        yield s


NOW = datetime.now(hours.TZ).replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)  # the café is open at 10:00 every day


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The café's clock reads 10:00 today, so order and booking rules don't depend on when the tests run."""
    monkeypatch.setattr(hours, "local_now", lambda: NOW)
    return NOW


class FakeLLM:
    """Each queued turn is fn(history, call_tool) -> reply text. Records every history it was given."""

    def __init__(self):
        self.turns, self.calls = [], []

    def run(self, system, history, tools, call_tool, usage=None):
        self.calls.append(history)
        return self.turns.pop(0)(history, call_tool) if self.turns else "ok"


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "_llm", fake)
    return fake


@pytest.fixture
def graph(monkeypatch):
    """Mock Graph API. Returns the list of requests sent."""
    sent = []

    def handler(req: httpx.Request):
        sent.append(req)
        if req.url.path.endswith("/media-1"):
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/voice.ogg", "mime_type": "audio/ogg; codecs=opus"})
        if req.url.path.endswith("/photo-1"):
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/photo.jpg", "mime_type": "image/jpeg"})
        if req.url.host == "lookaside.fbsbx.com":
            return httpx.Response(200, content=b"OGGDATA")
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}], "id": "media-out"})

    monkeypatch.setattr(whatsapp, "http", httpx.Client(transport=httpx.MockTransport(handler)))
    return sent
