"""Chat with the agent in the terminal: python cli.py [phone]"""
import sys

from app.agent import respond
from app.db import init_db

init_db()
sys.stdout.reconfigure(encoding="utf-8")  # Bangla on Windows consoles
phone = sys.argv[1] if len(sys.argv) > 1 else "01700000000"
print("Type a message (Ctrl+C to quit). Chat shows in the dashboard as a simulator session.")
while True:
    text = input("\nyou> ").strip()
    if text:
        reply = respond(f"sim-{phone}", "sim", phone, text)
        print("bot>", reply.text if reply else "(bot is off for this chat: handed to a human)")
