import os
from pathlib import Path

from dotenv import load_dotenv

from . import tenancy

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def cents(amount) -> int:
    """Money is stored as whole cents; business.yaml and the dashboard use normal amounts like 4.50."""
    return round(float(amount) * 100)


def money(amount_cents: int) -> str:
    return f"{tenancy.cfg().get('currency', '$')}{amount_cents / 100:,.2f}"


DATABASE_URL = env("DATABASE_URL", f"sqlite:///{ROOT / 'backend' / 'shop.db'}")
MEDIA_DIR = Path(env("MEDIA_DIR", str(ROOT / "backend" / "media")))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

LLM_PROVIDER = env("LLM_PROVIDER", "bedrock")  # bedrock | anthropic | ollama

# One Meta app for every café: it signs and verifies the webhooks. Each café's own WhatsApp number, Page and tokens
# live on its row in the shops table (manage.py connect).
WHATSAPP_VERIFY_TOKEN = env("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_APP_SECRET = env("WHATSAPP_APP_SECRET")
GRAPH_API_VERSION = env("GRAPH_API_VERSION", "v21.0")
PUBLIC_URL = env("PUBLIC_URL").rstrip("/")  # e.g. https://mitu.example.com; Messenger/Instagram fetch photos from here

# The first café: seed.py creates it from these when the database has none (see README).
BUSINESS_FILE = Path(env("BUSINESS_FILE", str(ROOT / "business.yaml")))
FIRST_SHOP_ID = env("SHOP_ID", "default")
ADMIN_PASSWORD = env("ADMIN_PASSWORD")  # the first café's Owner login
STAFF = {name.strip(): pw.strip() for name, _, pw in (pair.partition(":") for pair in env("STAFF").split(",")) if name.strip() and pw.strip()}
WHATSAPP_TOKEN = env("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID")
PAGE_ACCESS_TOKEN = env("PAGE_ACCESS_TOKEN")
PAGE_ID = env("PAGE_ID")
INSTAGRAM_ID = env("INSTAGRAM_ID")

STT_PROVIDER = env("STT_PROVIDER", "openai")  # openai | aws
STT_MIN_CONFIDENCE = float(env("STT_MIN_CONFIDENCE", "0.5"))
VOICE_MAX_SECONDS = 120
VOICE_REPLY = env("VOICE_REPLY", "false").lower() == "true"
