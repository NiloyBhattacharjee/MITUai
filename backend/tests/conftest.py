import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp / 'test.db'}", MEDIA_DIR=str(_tmp / "media"),
                  WHATSAPP_APP_SECRET="test-secret", WHATSAPP_VERIFY_TOKEN="verify-me", VOICE_REPLY="false", ALERT_EMAIL="",
                  PUBLIC_URL="https://shop.test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hashlib  # noqa: E402
from datetime import datetime  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import auth, hours, llm, shops, tenancy, whatsapp  # noqa: E402
from app.config import BUSINESS_FILE  # noqa: E402
from app.db import Base, LoginSession, Session, Staff, engine  # noqa: E402

auth.SCRYPT_N = 2**4  # fast hashes in tests; real ones use 2**14
STAFF = {"Owner": "pw", "Rina": "rina-pw", "Karim": "karim-pw"}
CHANNELS = dict(whatsapp_phone_number_id="123", whatsapp_token="tok", page_id="page-1", instagram_id="ig-1", page_token="page-tok")


def add_cafe(db, shop_id: str, staff: dict[str, str], **channels):
    """A café from business.yaml whose staff are signed in: each one's session token is their password, so tests send
    {"X-Session": "pw"} for the owner (logging in itself is tested separately)."""
    shop = shops.create(db, shop_id, shops.read(BUSINESS_FILE), staff, **channels)
    for person in db.query(Staff).filter(Staff.shop_id == shop_id):
        db.add(LoginSession(token_hash=hashlib.sha256(staff[person.name].encode()).hexdigest(), staff_id=person.id))
    db.commit()
    return shop


@pytest.fixture(autouse=True)
def db():
    """A fresh database with the café little-fern, which every test runs as (see app/tenancy.py)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as s:
        shop = add_cafe(s, "little-fern", STAFF, **CHANNELS)
        with tenancy.scope(shop):
            yield s


@pytest.fixture
def cafe():
    return tenancy.shop()


@pytest.fixture(autouse=True)
def clock(monkeypatch, db):
    """The café's clock reads 10:00 today, so order and booking rules don't depend on when the tests run."""
    now = datetime.now(hours.tz()).replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)  # open at 10:00 every day
    monkeypatch.setattr(hours, "local_now", lambda: now)
    return now


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
