"""Chat with the agent in the terminal: python cli.py [phone] [--shop ID]   (default: the first business)"""
import sys

from app import shops, tenancy
from app.agent import respond
from app.db import init_db

init_db()
sys.stdout.reconfigure(encoding="utf-8")  # emoji and accents on Windows consoles
args = [a for a in sys.argv[1:] if a != "--shop"]
shop = shops.pick(sys.argv[sys.argv.index("--shop") + 1] if "--shop" in sys.argv else None)
phone = next((a for a in args if a != shop.id), "0400000000")
print(f"Chatting with {shop.config['name']} (Ctrl+C to quit). It shows in the dashboard as a simulator chat.")
with tenancy.scope(shop):
    while True:
        text = input("\nyou> ").strip()
        if text:
            reply = respond(f"{shop.id}:sim-{phone}", "sim", phone, text)
            print("bot>", reply.text if reply else "(the AI is off for this chat: handed to a person)")
