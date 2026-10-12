import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


BUSINESS = yaml.safe_load(Path(env("BUSINESS_FILE", str(ROOT / "business.yaml"))).read_text(encoding="utf-8"))

DATABASE_URL = env("DATABASE_URL", f"sqlite:///{ROOT / 'backend' / 'shop.db'}")
MEDIA_DIR = Path(env("MEDIA_DIR", str(ROOT / "backend" / "media")))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
ADMIN_PASSWORD = env("ADMIN_PASSWORD")  # required: the server refuses to start without a real one

LLM_PROVIDER = env("LLM_PROVIDER", "bedrock")  # bedrock | anthropic | ollama

WHATSAPP_TOKEN = env("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = env("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_APP_SECRET = env("WHATSAPP_APP_SECRET")
GRAPH_API_VERSION = env("GRAPH_API_VERSION", "v21.0")

# Facebook Messenger + Instagram DMs: the Facebook Page's access token (the Instagram account must be linked to the Page).
PAGE_ACCESS_TOKEN = env("PAGE_ACCESS_TOKEN")
PUBLIC_URL = env("PUBLIC_URL").rstrip("/")  # e.g. https://shop.example.com; Messenger/Instagram fetch photos from here

# Dashboard logins: the owner uses ADMIN_PASSWORD; staff are "Name:password" pairs, comma separated.
STAFF = {"Owner": ADMIN_PASSWORD, **{name.strip(): pw.strip() for name, _, pw in
                                    (pair.partition(":") for pair in env("STAFF").split(",")) if name.strip() and pw.strip()}}

STT_PROVIDER = env("STT_PROVIDER", "openai")  # openai | aws
STT_MIN_CONFIDENCE = float(env("STT_MIN_CONFIDENCE", "0.5"))
VOICE_MAX_SECONDS = 120
VOICE_REPLY = env("VOICE_REPLY", "false").lower() == "true"
