"""Demo mode — deterministic fake data for anonymized screenshots.

Enabled with OMALINK_DEMO=1. Real contact names, phone numbers, and
message text are replaced with stable stand-ins (same input always maps
to the same fake output) so the UI looks natural in a marketing shot
without exposing anyone's data. Never affects what's sent to the phone.
"""

import hashlib
import os

ENABLED = os.environ.get("OMALINK_DEMO") == "1"

_NAMES = [
    "Jordan Lee", "Sam Rivera", "Alex Chen", "Morgan Diaz", "Casey Brooks",
    "Taylor Kim", "Riley Nguyen", "Jamie Park", "Drew Patel", "Quinn Foster",
    "Avery Santos", "Reese Walker", "Emerson Cole", "Harper Vance", "Rowan Ellis",
    "Skyler Reed", "Devon Hale", "Marlowe Fox", "Sage Bennett", "Nico Marsh",
]

_SNIPPETS = [
    "Sounds good, see you then!", "Can you send that over when you get a sec?",
    "Running about 10 minutes late", "Thanks so much for the help today",
    "Just landed, heading to the office", "Did you get a chance to look at it?",
    "Perfect, that works for me", "Let's grab lunch this week",
    "On my way now", "Talk tomorrow", "Great catching up earlier",
    "I'll have it ready by Friday", "Yes — go ahead and book it",
    "Your order is confirmed for pickup", "Reminder: appointment at 2pm",
]

_GROUP_SIZES = {}


def _pick(pool, key):
    h = int(hashlib.md5(str(key).encode()).hexdigest(), 16)
    return pool[h % len(pool)]


def fake_number(addr):
    h = int(hashlib.md5(str(addr).encode()).hexdigest(), 16)
    return f"+1 555-01{h % 100:02d}"


def fake_name(addresses):
    """One or more fake names for a thread's addresses (joined for groups)."""
    seen = []
    for a in addresses:
        n = _pick(_NAMES, a)
        if n not in seen:
            seen.append(n)
    return ", ".join(seen) if seen else "Contact"


def fake_snippet(key):
    return _pick(_SNIPPETS, key)


# Fake notifications for the sidebar. Shaped like kdeconnect.Notification
# (nid, app_name, title, text, dismissable).
class _Notif:
    def __init__(self, nid, app_name, title, text):
        self.nid, self.app_name, self.title, self.text = nid, app_name, title, text
        self.dismissable = True
        self.reply_id = ""


NOTIFICATIONS = [
    _Notif("d1", "Messages", "Sam Rivera", "Running about 10 minutes late"),
    _Notif("d2", "Calendar", "Standup", "Starts in 15 minutes · Meeting room 2"),
    _Notif("d3", "Maps", "Traffic alert", "Slower than usual on your route home"),
]

