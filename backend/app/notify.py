"""Flag a chat for the shop team: it shows in the dashboard's Conversations."""


def flag_for_human(db, conv, reason: str, stop_bot=False):
    conv.needs_human, conv.handoff_reason = True, reason
    if stop_bot:
        conv.bot_enabled = False
    db.commit()
